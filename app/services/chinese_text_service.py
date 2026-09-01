"""本地转写中文本的字形标准化。"""

from functools import lru_cache


SIMPLIFIED_CHINESE_NORMALIZATION_ID = "opencc-t2s-v1"


@lru_cache(maxsize=1)
def _t2s_converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError(
            "未安装简体转换依赖 opencc。请先安装 requirements.txt，再重新生成本地转写。"
        ) from exc
    return OpenCC("t2s.json")


def simplify_chinese_text(text: str) -> str:
    """只做繁体到简体的字形转换，不做大陆词汇本地化。"""
    if not text:
        return text
    return _t2s_converter().convert(text)
