import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai.ai_clip_analyzer import build_provider  # noqa: E402


def main() -> None:
    provider = build_provider("remote")
    prompt = (
        "请只输出严格 JSON："
        '{"status":"ok","provider":"remote","message":"connection test"}'
    )
    text = provider.generate_json(prompt)
    payload = json.loads(text)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
