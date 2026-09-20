"""Export the public citation schema and a synthetic replay fixture for clients."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent_runtime.ledger.artifacts import artifact_event_payload  # noqa: E402
from app.agent_runtime.ledger.models import AgentArtifact  # noqa: E402
from app.rednote.schemas import RedNoteCard, RedNotePost  # noqa: E402


def documents() -> dict[str, str]:
    post = RedNotePost(title="测试帖子：返岗准备经验", summary="这是一条仅用于客户端契约验证的虚构摘要，描述提前准备配件和合理安排时间的个人经验。",
        author="测试作者", favorites=123, url="https://www.xiaohongshu.com/explore/000000000000000000000001",
        relevance_score=.92)
    artifact = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", artifact_type="rednote_posts",
        schema_version="v1", status="ready", raw_payload_ref="",
        payload={"card": RedNoteCard(posts=[post]).model_dump(mode="json")})
    values = {"rednote-posts.schema.json": RedNoteCard.model_json_schema(),
        "rednote-posts.example.json": {"type": "artifact.created", "payload": artifact_event_payload(cast(AgentArtifact, artifact))}}
    return {name: json.dumps(value, ensure_ascii=False, indent=2)+"\n" for name, value in values.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, content in documents().items():
        target = ROOT / "docs" / name
        if args.check:
            if not target.exists() or target.read_text() != content:
                raise SystemExit(f"RedNote contract drift: {name}")
        else:
            target.write_text(content)


if __name__ == "__main__":
    main()
