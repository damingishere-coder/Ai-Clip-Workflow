"""Real editor and markup, with isolated deterministic slow/failing HTTP responses."""
import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("output_batch", ["review"], indirect=True)
@pytest.mark.parametrize("failure,save_first,approve_first", [(False, False, False), (False, True, False), (True, False, False), (False, False, True)])
def test_subtitle_switch_flushes_pending_or_inflight_save_and_keeps_failure(output_batch, failure, save_first, approve_first):
    playwright = pytest.importorskip("playwright.async_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("Chrome unavailable")
    task, _, _ = output_batch
    html = TestClient(app).get(f"/subtitles/{task}").text.replace("http://testserver", "http://studio.test")

    async def scenario():
        async with playwright.async_playwright() as runtime:
            browser = await runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = await browser.new_page()
            errors, writes, events = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            revisions = {name: {"id": f"revision-{name}", "revision_number": 1, "status": "draft",
                               "cues": [{"id": f"cue-{name}", "start_ms": 1000, "end_ms": 3000, "text": name}]}
                         for name in ["source", "clip"]}
            tracks = [{"id": name, "track_type": "source" if name == "source" else "output", "name": name,
                       "sync_status": "synced", "media_url": "/fake.mp4", "peaks_url": f"/peaks/{name}"}
                      for name in revisions]

            async def respond(route):
                path = route.request.url.split("studio.test", 1)[1].split("?", 1)[0]
                if path == f"/subtitles/{task}":
                    return await route.fulfill(body=html, content_type="text/html")
                if path.startswith("/static/"):
                    file = ROOT / "app" / path.lstrip("/")
                    if file.is_file():
                        mime = "text/javascript" if file.suffix == ".js" else "text/css" if file.suffix == ".css" else "application/octet-stream"
                        return await route.fulfill(body=file.read_bytes(), content_type=mime)
                    return await route.fulfill(status=404)
                if path.endswith("/tracks"):
                    payload = {"tracks": tracks}
                elif path.endswith("/cues"):
                    name = path.split("/")[-2]
                    events.append(f"load-{name}")
                    payload = {"track": next(item for item in tracks if item["id"] == name), "revision": revisions[name],
                               "cues": revisions[name]["cues"], "total": 1}
                elif path.endswith("/revisions"):
                    name = path.split("/")[-2]
                    events.append("save-start")
                    writes.append(route.request.post_data_json)
                    await asyncio.sleep(.4)
                    if failure:
                        return await route.fulfill(status=409, json={"detail": "current version changed"})
                    revisions[name] = {**revisions[name], "id": f"saved-{name}", "revision_number": 2,
                                           "cues": route.request.post_data_json["cues"]}
                    events.append("save-end")
                    payload = {"revision": revisions[name]}
                elif path.endswith("/approve"):
                    events.append("approve-start")
                    await asyncio.sleep(.4)
                    revisions["source"] = {**revisions["source"], "id": "approved-source", "status": "approved"}
                    payload = {"revision": revisions["source"]}
                elif path.startswith("/peaks/"):
                    return await route.fulfill(status=503, json={"detail": "isolated media"})
                else:
                    payload = {"jobs": []}
                await route.fulfill(body=json.dumps(payload), content_type="application/json")

            await page.route("http://studio.test/**", respond)
            await page.goto(f"http://studio.test/subtitles/{task}")
            editor = page.locator("#subtitle-cue-viewport textarea").first
            await editor.wait_for()
            await editor.fill("马上切换前的人工修改")
            if approve_first:
                await page.locator("#subtitle-approve").click()
                for _ in range(30):
                    if "approve-start" in events:
                        break
                    await page.wait_for_timeout(30)
                assert "approve-start" in events
                assert await page.locator("#subtitle-track-select").is_disabled()
                # Even a queued change event cannot redirect an in-flight source-track action.
                await page.locator("#subtitle-track-select").evaluate("el => { el.value = 'clip'; el.dispatchEvent(new Event('change')); }")
                await page.wait_for_timeout(500)
                assert await page.locator("#subtitle-track-select").input_value() == "source"
                assert "load-clip" not in events
            if save_first:
                await page.locator("#subtitle-save-now").click()
                await page.wait_for_timeout(80)
            await page.locator("#subtitle-track-select").select_option("clip")
            await page.wait_for_timeout(750)
            assert len(writes) == 1
            assert writes[0]["cues"][0]["text"] == "马上切换前的人工修改"
            if failure:
                assert await page.locator("#subtitle-track-select").input_value() == "source"
                assert await editor.input_value() == "马上切换前的人工修改"
                assert "版本冲突" in await page.locator("#subtitle-save-state").inner_text()
                assert "load-clip" not in events
            else:
                assert await page.locator("#subtitle-track-select").input_value() == "clip"
                assert events.index("save-end") < events.index("load-clip")
                assert await editor.input_value() == "clip"
                if approve_first:
                    await editor.fill("切片自己的人工修改")
                    await page.locator("#subtitle-save-now").click()
                    await page.wait_for_timeout(500)
                    assert writes[-1]["base_revision_id"] == "revision-clip"
            assert not errors
            await browser.close()

    asyncio.run(scenario())
