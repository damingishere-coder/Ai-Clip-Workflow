import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class PublishProviderError(Exception):
    def __init__(self, message: str, error_code: str = "provider_error", response: dict | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.response = response or {}


@dataclass
class PublishResult:
    upload_id: str = ""
    item_id: str = ""
    audit_status: str = "submitted"
    response: dict | None = None


class DouyinPublishProvider:
    def __init__(self, config: dict):
        self.config = config

    def validate_config(self) -> None:
        required = {
            "client_key": "抖音 Client Key",
            "client_secret": "抖音 Client Secret",
            "upload_url": "抖音视频上传接口",
            "create_url": "抖音创建视频接口",
        }
        missing = [label for key, label in required.items() if not (self.config.get(key) or "").strip()]
        if missing:
            raise PublishProviderError(f"缺少配置：{'、'.join(missing)}。", "missing_config")

    def build_oauth_url(self, state: str) -> str:
        self.validate_config()
        auth_url = (self.config.get("auth_url") or "https://open.douyin.com/platform/oauth/connect/").strip()
        redirect_uri = (self.config.get("redirect_uri") or "").strip()
        if not redirect_uri:
            raise PublishProviderError("请先在抖音后台配置 OAuth 回调地址。", "missing_redirect_uri")
        params = {
            "client_key": self.config.get("client_key"),
            "response_type": "code",
            "scope": self.config.get("scope") or "video.create,video.upload,user_info",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{auth_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        self.validate_config()
        token_url = (self.config.get("token_url") or "").strip()
        if not token_url:
            raise PublishProviderError("请先配置抖音 token 接口地址。", "missing_token_url")
        payload = {
            "client_key": self.config.get("client_key"),
            "client_secret": self.config.get("client_secret"),
            "code": code,
            "grant_type": "authorization_code",
        }
        return _post_json(token_url, payload)

    def publish(self, account: dict, job: dict, video_path: Path) -> PublishResult:
        self.validate_config()
        access_token = (account.get("access_token") or "").strip()
        open_id = (account.get("open_id") or "").strip()
        if not access_token:
            raise PublishProviderError("抖音账号还没有 access_token，请先完成授权。", "missing_access_token")
        if not open_id:
            raise PublishProviderError("抖音账号缺少 open_id，请重新授权后再发布。", "missing_open_id")

        upload_url = (self.config.get("upload_url") or "").strip()
        create_url = (self.config.get("create_url") or "").strip()
        upload_response = _post_multipart(
            upload_url,
            fields={"open_id": open_id},
            file_field="video",
            file_path=video_path,
            headers={"access-token": access_token},
        )
        video_id = _find_first(upload_response, ["video_id", "data.video_id", "data.video.video_id", "video.video_id"])
        if not video_id:
            raise PublishProviderError("抖音上传成功但没有返回 video_id。", "missing_video_id", upload_response)

        title_text = _compose_title_text(job)
        create_payload = {
            "open_id": open_id,
            "video_id": video_id,
            "title": job.get("title") or "",
            "text": title_text,
            "visibility_type": job.get("visibility") or "public",
            "is_allow_download": bool(job.get("allow_download")),
        }
        if job.get("cover_mode") == "time":
            create_payload["video_cover_time_ms"] = int(float(job.get("cover_time_seconds") or 0) * 1000)
        create_response = _post_json(
            create_url,
            create_payload,
            headers={"access-token": access_token},
        )
        item_id = _find_first(create_response, ["item_id", "data.item_id", "data.item.item_id", "share_id"])
        return PublishResult(
            upload_id=str(video_id),
            item_id=str(item_id or ""),
            audit_status="submitted",
            response={"upload": upload_response, "create": create_response},
        )


class BilibiliPublishProvider:
    def __init__(self, config: dict):
        self.config = config

    def validate_config(self) -> None:
        required = {
            "client_key": "B站开放平台 Client Key",
            "client_secret": "B站开放平台 Client Secret",
            "upload_url": "B站视频上传接口",
            "create_url": "B站稿件发布接口",
        }
        missing = [label for key, label in required.items() if not (self.config.get(key) or "").strip()]
        if missing:
            raise PublishProviderError(
                f"B站真实投稿接口还未配置完整：{'、'.join(missing)}。完成开放平台入驻并拿到接口文档后，在这里补齐即可。",
                "missing_bilibili_config",
            )

    def publish(self, account: dict, job: dict, video_path: Path) -> PublishResult:
        self.validate_config()
        if not (account.get("access_token") or "").strip():
            raise PublishProviderError("B站账号还没有 access_token，请先完成开放平台授权。", "missing_access_token")
        raise PublishProviderError(
            "B站 provider 已准备好配置、字段校验和任务状态，但当前项目还没有你的 B站开放平台投稿接口权限，暂不执行真实上传。",
            "bilibili_provider_pending",
        )


def _compose_title_text(job: dict) -> str:
    parts = [job.get("title") or ""]
    tags = (job.get("tags") or "").replace("，", ",")
    tag_text = " ".join(f"#{tag.strip().lstrip('#')}" for tag in tags.split(",") if tag.strip())
    if tag_text:
        parts.append(tag_text)
    description = (job.get("description") or "").strip()
    if description:
        parts.append(description)
    return "\n".join(part for part in parts if part).strip()


def _find_first(payload: dict, paths: list[str]) -> object | None:
    for path in paths:
        cursor: object = payload
        for part in path.split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                cursor = None
                break
        if cursor not in (None, ""):
            return cursor
    return None


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request_json(url, body, request_headers)


def _post_multipart(
    url: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    headers: dict | None = None,
) -> dict:
    boundary = f"----LiveSlicingBoundary{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    request_headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        **(headers or {}),
    }
    return _request_json(url, body, request_headers)


def _request_json(url: str, body: bytes, headers: dict) -> dict:
    request = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = _parse_json(raw)
        raise PublishProviderError(f"平台接口返回 HTTP {exc.code}：{_extract_error_message(parsed, raw)}", str(exc.code), parsed) from exc
    except urllib.error.URLError as exc:
        raise PublishProviderError(f"平台接口请求失败：{exc.reason}", "network_error") from exc

    parsed = _parse_json(raw)
    error_message = _extract_error_message(parsed, "")
    if error_message:
        raise PublishProviderError(error_message, str(parsed.get("err_no") or parsed.get("error_code") or "api_error"), parsed)
    return parsed


def _parse_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _extract_error_message(payload: dict, fallback: str) -> str:
    code = payload.get("err_no", payload.get("error_code", payload.get("code")))
    message = payload.get("err_msg") or payload.get("error_msg") or payload.get("message") or payload.get("description")
    if code in (None, 0, "0", ""):
        return ""
    return str(message or fallback or f"平台返回错误码 {code}")
