"""Windows Worker 与旧兼容流程共用的页面脚本入口。

旧版发送流程在 ``publish_service`` 中积累了经过真实页面反复修正的 DOM 脚本。
这里通过延迟导入暴露同一份脚本，避免 Playwright Publisher 再维护一套脆弱选择器，
同时也避免模块加载时形成循环依赖。
"""

from __future__ import annotations

import json
from typing import Any


def _legacy_module():
    from app.services import publish_service

    return publish_service


def fill_title(title: str) -> str:
    legacy = _legacy_module()
    return legacy._fill_visible_field_script(legacy._TITLE_FIELD_SELECTOR, title, "title")


def douyin_description(job: dict[str, Any], title: str) -> str:
    return _legacy_module()._douyin_description_for_job(job, title)


def douyin_close_preview_tip() -> str:
    return _legacy_module()._douyin_close_preview_tip_script()


def douyin_upload_state() -> str:
    """读取抖音上传/解析状态；常驻表单文案永远不能代表上传完成。"""

    return (
        "(()=>{"
        "const visible=(el)=>{const style=getComputedStyle(el);const rect=el.getBoundingClientRect();return style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>String(el?.innerText||el?.textContent||'').replace(/\\s+/g,'').trim();"
        "const body=textOf(document.body);"
        "const failures=['上传失败','文件格式错误','文件格式不支持','不支持该视频','视频处理失败','解析失败','转码失败','网络异常，请重试'];"
        "const failure=failures.find((item)=>body.includes(item));"
        "if(failure){return {state:'failed',upload_ready:false,error_code:'douyin_video_upload_failed',message:failure};}"
        "const progressNodes=[...document.querySelectorAll('span,div,p')].filter(visible).map(textOf).filter((text)=>/^\\d{1,3}%$/.test(text));"
        "const progressValues=progressNodes.map((text)=>Number(text.slice(0,-1))).filter(Number.isFinite);"
        "const progress=progressValues.length?Math.min(...progressValues):null;"
        "const busyMarkers=['文件解析中','正在上传','上传中','视频处理中','正在处理','转码中','等待上传','请等待上传完成','上传过程中请不要删除','上传过程中请勿删除'];"
        "const explanatoryMarkers=['点击发布后','如作品还在上传中','上传发布完成','视频预览功能','实际播放时'];"
        "const statusTexts=[...document.querySelectorAll('span,div,p')].filter(visible).map(textOf).filter((text)=>text&&text.length<=40&&!explanatoryMarkers.some((item)=>text.includes(item)));"
        "const busy=busyMarkers.find((item)=>statusTexts.some((text)=>text===item||text.startsWith(`${item}，`)||text.startsWith(`${item},`)||text.startsWith(`${item}：`)||text.startsWith(`${item}:`)||text.startsWith(`${item}...`)||text.startsWith(`${item}…`)))||((progress!==null&&progress<100)?`${progress}%`:'');"
        "const badImage=(src)=>/logo|avatar|favicon|icon|douyin-creator-logo|static\\/image/i.test(src||'');"
        "const videos=[...document.querySelectorAll('video')].filter((el)=>visible(el)&&(el.videoWidth>0||el.readyState>=2||Number.isFinite(el.duration)));"
        "const canvases=[...document.querySelectorAll('canvas')].filter((el)=>{const rect=el.getBoundingClientRect();return visible(el)&&el.width>=160&&el.height>=90&&rect.width>=120&&rect.height>=80;});"
        "const images=[...document.querySelectorAll('img')].filter((el)=>{const rect=el.getBoundingClientRect();const src=el.currentSrc||el.src||'';return visible(el)&&!badImage(src)&&el.complete!==false&&el.naturalWidth>=240&&el.naturalHeight>=135&&rect.width>=120&&rect.height>=80;});"
        "const preview_count=videos.length+canvases.length+images.length;"
        "const upload_ready=!busy&&preview_count>0&&(progress===null||progress>=100);"
        "return {state:upload_ready?'ready':(busy?'processing':'waiting_preview'),upload_ready,progress,preview_count,busy_marker:busy||'',message:upload_ready?'视频上传与解析完成':(busy?`仍在上传或解析：${busy}`:'等待真实视频预览')};"
        "})()"
    )


def douyin_set_description(description: str) -> str:
    return _legacy_module()._douyin_set_description_script(description)


def douyin_verify_ready(title: str, description: str) -> str:
    return _legacy_module()._douyin_verify_publish_ready_script(title, description)


def douyin_set_visibility(visibility: str) -> str:
    labels = {"public": "公开", "friends": "好友可见", "private": "仅自己可见"}
    label = labels.get(str(visibility or "public"), "公开")
    return (
        "(async()=>{"
        f"const expected={json.dumps(label, ensure_ascii=False)};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>String(el?.innerText||el?.textContent||'').replace(/\\s+/g,'').trim();"
        "const isSelected=(el)=>{const input=el.matches?.('input')?el:el.querySelector?.('input[type=radio],input[type=checkbox]');return Boolean(input?.checked||el.getAttribute?.('aria-checked')==='true'||/(^|\\s)(active|checked|selected)(\\s|$)/i.test(String(el.className||'')));};"
        "const labels=['公开','好友可见','仅自己可见'];"
        "const optionNodes=[...document.querySelectorAll('label,button,[role=radio],[role=option],div,span')].filter(visible).filter((el)=>textOf(el)===expected);"
        "const scored=optionNodes.map((el)=>{const clickable=el.closest('label,button,[role=radio],[role=option]')||el;let score=0;let node=clickable;for(let i=0;i<7&&node;i+=1){const text=textOf(node);if(text.includes('谁可以看'))score+=100-i*8;if(labels.filter((item)=>text.includes(item)).length>=2)score+=40-i*3;node=node.parentElement;}if(clickable.matches('label,button,[role=radio],[role=option]'))score+=20;return {el:clickable,score};}).sort((a,b)=>b.score-a.score);"
        "const target=scored[0]?.el;if(!target){throw new Error('douyin_visibility_option_not_found:'+expected);}"
        "target.scrollIntoView({block:'center',inline:'center'});target.click();await sleep(700);"
        "const refreshed=[...document.querySelectorAll('label,button,[role=radio],[role=option],div,span')].filter(visible).filter((el)=>textOf(el)===expected).map((el)=>el.closest('label,button,[role=radio],[role=option]')||el);"
        "const selected=refreshed.find(isSelected)||refreshed.find((el)=>{let node=el;for(let i=0;i<3&&node;i+=1){if(isSelected(node))return true;node=node.parentElement;}return false;});"
        "if(!selected){throw new Error('douyin_visibility_not_applied:'+expected);}"
        "return {visibility_verified:true,visibility_text:expected,option_count:refreshed.length};"
        "})()"
    )


def douyin_wait_recommended_cover(timeout_seconds: int = 150) -> str:
    return _legacy_module()._douyin_wait_ai_cover_script(timeout_seconds)


def douyin_click_recommended_cover() -> str:
    return _legacy_module()._douyin_click_ai_cover_script()


def douyin_confirm_cover(timeout_seconds: int = 20) -> str:
    return _legacy_module()._douyin_confirm_cover_script(timeout_seconds)


def douyin_verify_cover(timeout_seconds: int = 45) -> str:
    return _legacy_module()._douyin_verify_cover_applied_script(timeout_seconds)


def douyin_click_publish() -> str:
    return _legacy_module()._douyin_click_publish_script()


def douyin_wait_result(title: str, timeout_seconds: int = 120) -> str:
    return _legacy_module()._douyin_wait_publish_result_script(title, timeout_seconds)


def bilibili_dismiss_local_draft() -> str:
    return _legacy_module()._bilibili_dismiss_local_draft_script()


def bilibili_wait_uploaded(timeout_seconds: int = 180) -> str:
    return _legacy_module()._bilibili_wait_video_uploaded_script(timeout_seconds)


def bilibili_select_recommended_cover(timeout_seconds: int = 120) -> str:
    return _legacy_module()._bilibili_select_recommended_cover_script(timeout_seconds)


def bilibili_select_declaration() -> str:
    return _legacy_module()._bilibili_select_declaration_script()


def bilibili_select_category(category: str) -> str:
    return _legacy_module()._bilibili_select_category_if_empty_script(category)


def bilibili_set_description(description: str) -> str:
    return _legacy_module()._bilibili_set_description_script(description)


def bilibili_verify_ready(title: str, description: str) -> str:
    return _legacy_module()._bilibili_verify_publish_ready_script(title, description)


def bilibili_click_publish() -> str:
    return _legacy_module()._bilibili_click_publish_script()


def bilibili_wait_result(title: str, timeout_seconds: int = 180) -> str:
    return _legacy_module()._bilibili_wait_publish_result_script(title, timeout_seconds)
