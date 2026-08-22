"""全自动模式的短视频标题文案生成器。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.publish_copy_rules import (
    BILIBILI_TITLE_MAX,
    build_generated_douyin_publish_copy,
    normalize_douyin_tags,
)
from app.services.publish_service import generate_publish_metadata


_RISK_KEYWORDS = {
    "低俗或脏话": ("傻逼", "傻x", "傻X", "屎", "尿", "屁", "低俗"),
    "暴力或血腥": ("暴力", "血腥", "杀人", "死亡", "自杀", "自残"),
    "色情或成人": ("色情", "黄色", "裸露", "约炮", "招嫖"),
    "赌博或诈骗": ("赌博", "博彩", "诈骗", "稳赚", "刷单"),
    "引战夸张": ("全网第一", "100%", "必看", "不看后悔", "震惊"),
}


@dataclass(frozen=True)
class MetadataGenerator:
    """把切片信息整理成发布中心可使用的结构化文案。"""

    use_ai: bool = False

    def generate(self, item: dict, platform: str) -> dict:
        metadata = generate_publish_metadata(item, use_ai=self.use_ai, platform=platform)
        title = self._polish_title(metadata.get("title") or "", item, platform)
        caption = self._polish_caption(metadata.get("description") or "", title, platform)
        hashtags = self._hashtags(metadata.get("tags") or "", item, platform)
        if platform == "douyin":
            normalized = build_generated_douyin_publish_copy(title, caption, hashtags)
            title = normalized["title"]
            caption = normalized["description"]
            hashtags = normalize_douyin_tags(normalized["tags"])
        cover_text = self._cover_text(title)
        risk_flags = self._risk_flags(item, title, caption, hashtags)
        return {
            "clip_id": item.get("output_clip_id") or item.get("id") or "",
            "clip_candidate_id": item.get("clip_candidate_id") or "",
            "platform": platform,
            "title": title,
            "caption": caption,
            "hashtags": hashtags,
            "cover_text": cover_text,
            "risk_flags": risk_flags,
            "status": "NEED_REVIEW" if risk_flags else "READY",
            "source": metadata.get("source") or "rule",
            "error": metadata.get("error") or "",
            "recommend_reason": item.get("highlight_reason") or item.get("clip_summary") or "",
        }

    def _polish_title(self, title: str, item: dict, platform: str) -> str:
        text = re.sub(r"\s+", " ", title or "").strip(" #＃")
        context = " ".join(
            str(item.get(key) or "")
            for key in ("task_name", "clip_title", "clip_summary", "highlight_reason")
        )
        if "康熙" in context and "康熙" not in text:
            text = f"康熙名场面：{text}" if text else "康熙来了经典名场面"
        text = re.sub(r"(震惊|不看后悔|全网第一|必看)", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ：:，,。.!！?？")
        max_length = 30 if platform == "douyin" else BILIBILI_TITLE_MAX
        return (text or "经典综艺高光片段")[:max_length]

    def _polish_caption(self, caption: str, title: str, platform: str) -> str:
        text = re.sub(r"\s+", " ", caption or "").strip()
        if not text:
            text = title
        return text[:35] if platform == "douyin" else text[:180]

    def _hashtags(self, tags: str, item: dict, platform: str) -> list[str]:
        raw_tags = re.split(r"[,，#＃\s]+", tags or "")
        context = " ".join(str(item.get(key) or "") for key in ("task_name", "clip_title", "clip_summary"))
        if "康熙" in context:
            raw_tags = [*raw_tags, "康熙", "综艺"]
        raw_tags = [*raw_tags, "高光", "看点"]
        if platform == "douyin":
            return normalize_douyin_tags(raw_tags, generated=True)
        cleaned: list[str] = []
        for tag in raw_tags:
            value = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(tag or "").strip().lstrip("#"))
            if not value or len(value) > 12:
                continue
            if value not in cleaned:
                cleaned.append(value)
            if len(cleaned) >= 8:
                break
        return cleaned or ["精彩片段", "高光片段"]

    def _cover_text(self, title: str) -> str:
        return title[:24] or "精彩片段"

    def _risk_flags(self, item: dict, title: str, caption: str, hashtags: list[str]) -> list[str]:
        text = " ".join(
            [
                str(item.get("clip_title") or ""),
                str(item.get("clip_summary") or ""),
                str(item.get("highlight_reason") or ""),
                title,
                caption,
                " ".join(hashtags),
            ]
        )
        flags = []
        for label, keywords in _RISK_KEYWORDS.items():
            if any(keyword.lower() in text.lower() for keyword in keywords):
                flags.append(label)
        return flags
