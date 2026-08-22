"""抖音发布文案的统一规则。

该模块只包含纯文本处理，供生成、保存、排期预检和最终发布共同复用。
"""

from __future__ import annotations

import re
from collections.abc import Iterable


PUBLISH_COPY_RULE_VERSION = 2

DOUYIN_TITLE_TARGET_MIN = 18
DOUYIN_TITLE_TARGET_MAX = 26
DOUYIN_TITLE_MAX = 30
DOUYIN_DESCRIPTION_MIN = 15
DOUYIN_DESCRIPTION_MAX = 35
DOUYIN_TAG_COUNT_MIN = 4
DOUYIN_TAG_COUNT_MAX = 6
DOUYIN_TAG_LENGTH_MIN = 2
DOUYIN_TAG_LENGTH_MAX = 3
BILIBILI_TITLE_MAX = 80

DOUYIN_FALLBACK_TAGS = ("综艺", "高光", "笑点", "看点", "趣事", "反转")
_CLICHE_PHRASES = (
    "现场爆笑",
    "全场爆笑",
    "笑翻全场",
    "引发热议",
    "不容错过",
    "反应强烈",
    "气氛拉满",
)
_EMPTY_TAG_PHRASES = (
    "这是一段",
    "这是",
    "标题",
    "简介",
    "解释",
    "适合",
    "内容说明",
)


def split_publish_tags(tags: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if isinstance(tags, str):
        hashtag_tags = re.findall(r"[#＃]\s*([^#＃,，\s]+)", tags)
        values: Iterable[str] = hashtag_tags if hashtag_tags else re.split(r"[,，#＃\s]+", tags)
    else:
        values = tags or []
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def normalize_douyin_title(value: str | None, *, generated: bool = False, fallback: str = "精彩片段") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" #＃：:，,。.!！?？")
    text = re.sub(r"(震惊|不看后悔|全网第一|必看)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" #＃：:，,。.!！?？") or fallback
    if generated:
        return text[:DOUYIN_TITLE_MAX]
    return text


def normalize_douyin_description(
    value: str | None,
    *,
    title: str = "",
    generated: bool = False,
) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" #＃，,。.!！?？")
    for phrase in _CLICHE_PHRASES:
        text = text.replace(phrase, "")
    text = re.sub(r"([，,。.!！?？])\1+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" #＃，,。.!！?？")
    if not generated:
        return text

    if len(text) > DOUYIN_DESCRIPTION_MAX:
        window = text[: DOUYIN_DESCRIPTION_MAX + 1]
        cut_at = max(window.rfind(mark) for mark in "，。！？；")
        if cut_at >= DOUYIN_DESCRIPTION_MIN:
            text = window[:cut_at]
        else:
            text = text[:DOUYIN_DESCRIPTION_MAX]
    if len(text) < DOUYIN_DESCRIPTION_MIN:
        supplement = normalize_douyin_title(title, generated=True)
        text = f"{text}，{supplement}".strip("，") if text else supplement
    if len(text) < DOUYIN_DESCRIPTION_MIN:
        text = f"{text}，这一刻的反应很有看点".strip("，")
    return text[:DOUYIN_DESCRIPTION_MAX].strip(" #＃，,。.!！?？")


def normalize_douyin_tags(
    tags: list[str] | tuple[str, ...] | str | None,
    *,
    generated: bool = False,
) -> list[str]:
    cleaned: list[str] = []
    for raw_tag in split_publish_tags(tags):
        value = re.sub(r"[^\w\u4e00-\u9fff]+", "", raw_tag.lstrip("#＃"))
        if not value or any(phrase in value for phrase in _EMPTY_TAG_PHRASES):
            continue
        if not DOUYIN_TAG_LENGTH_MIN <= len(value) <= DOUYIN_TAG_LENGTH_MAX:
            continue
        if value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= DOUYIN_TAG_COUNT_MAX:
            break
    if generated:
        for fallback in DOUYIN_FALLBACK_TAGS:
            if fallback not in cleaned:
                cleaned.append(fallback)
            if len(cleaned) >= DOUYIN_TAG_COUNT_MIN:
                break
    return cleaned[:DOUYIN_TAG_COUNT_MAX]


def format_douyin_tags(tags: list[str] | tuple[str, ...] | str | None, *, generated: bool = False) -> str:
    return ", ".join(normalize_douyin_tags(tags, generated=generated))


def validate_douyin_publish_copy(title: str, description: str, tags: list[str] | str | None) -> None:
    normalized_title = re.sub(r"\s+", " ", str(title or "")).strip()
    normalized_description = re.sub(r"\s+", " ", str(description or "")).strip()
    raw_tags = split_publish_tags(tags)
    normalized_tags = normalize_douyin_tags(raw_tags)

    if not normalized_title:
        raise ValueError("抖音标题不能为空")
    if len(normalized_title) > DOUYIN_TITLE_MAX:
        raise ValueError(f"抖音标题不能超过 {DOUYIN_TITLE_MAX} 字，当前为 {len(normalized_title)} 字")
    if not DOUYIN_DESCRIPTION_MIN <= len(normalized_description) <= DOUYIN_DESCRIPTION_MAX:
        raise ValueError(
            f"抖音简介需为 {DOUYIN_DESCRIPTION_MIN}～{DOUYIN_DESCRIPTION_MAX} 字，"
            f"当前为 {len(normalized_description)} 字"
        )
    if len(raw_tags) != len(normalized_tags):
        raise ValueError("抖音标签必须去重，且每个标签严格为 2～3 字")
    if not DOUYIN_TAG_COUNT_MIN <= len(normalized_tags) <= DOUYIN_TAG_COUNT_MAX:
        raise ValueError(f"抖音标签需填写 {DOUYIN_TAG_COUNT_MIN}～{DOUYIN_TAG_COUNT_MAX} 个")


def build_douyin_publish_copy(
    title: str | None,
    description: str | None,
    tags: list[str] | tuple[str, ...] | str | None,
) -> dict[str, str]:
    normalized_title = normalize_douyin_title(title)
    normalized_description = normalize_douyin_description(description)
    normalized_tags = format_douyin_tags(tags)
    validate_douyin_publish_copy(normalized_title, normalized_description, normalized_tags)
    return {
        "title": normalized_title,
        "description": normalized_description,
        "tags": normalized_tags,
    }


def build_generated_douyin_publish_copy(
    title: str | None,
    description: str | None,
    tags: list[str] | tuple[str, ...] | str | None,
) -> dict[str, str]:
    normalized_title = normalize_douyin_title(title, generated=True)
    normalized_description = normalize_douyin_description(
        description,
        title=normalized_title,
        generated=True,
    )
    normalized_tags = format_douyin_tags(tags, generated=True)
    validate_douyin_publish_copy(normalized_title, normalized_description, normalized_tags)
    return {
        "title": normalized_title,
        "description": normalized_description,
        "tags": normalized_tags,
    }
