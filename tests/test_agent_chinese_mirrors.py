"""Chinese review copies stay in sync but never become runtime instructions."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.agent import AGENT, SERVICE_SKILL_REGISTRY


AGENT_ROOT = Path(__file__).parents[1] / "app" / "agent"
MIRROR_ROOT = Path(__file__).parents[1] / "docs" / "agent-zh"
SOURCE_PATTERN = re.compile(r"^<!-- 中文对照；英文源文件 SHA-256: ([a-f0-9]{64})；仅供审阅，不参与运行时加载。 -->$")


def test_every_runtime_instruction_has_one_current_chinese_review_copy() -> None:
    sources = {Path("system_prompt.md"), *(
        path.relative_to(AGENT_ROOT) for path in (AGENT_ROOT / "skills").rglob("*.md")
    )}
    mirrors = {path.relative_to(MIRROR_ROOT) for path in MIRROR_ROOT.rglob("*.md") if path.name != "README.md"}
    assert mirrors == sources

    for relative_path in sources:
        source = (AGENT_ROOT / relative_path).read_bytes()
        translated = (MIRROR_ROOT / relative_path).read_text(encoding="utf-8")
        match = SOURCE_PATTERN.fullmatch(translated.splitlines()[0])
        assert match is not None, relative_path
        assert match.group(1) == hashlib.sha256(source).hexdigest(), relative_path
        assert translated.count("<!-- 中文对照；英文源文件 SHA-256:") == 1, relative_path
        assert any("\u4e00" <= char <= "\u9fff" for char in translated), relative_path
        if relative_path != Path("system_prompt.md"):
            source_text = source.decode("utf-8")
            source_name = re.search(r"^name: ([a-z0-9-]+)$", source_text, re.M)
            mirror_name = re.search(r"^name: ([a-z0-9-]+)$", translated, re.M)
            assert source_name is not None and mirror_name is not None, relative_path
            assert source_name.group(1) == mirror_name.group(1), relative_path
            assert set(re.findall(r"`([a-z0-9-]+\.md)`", source_text)) == set(
                re.findall(r"`([a-z0-9-]+\.md)`", translated)
            ), relative_path


def test_chinese_review_copies_are_not_loaded_into_runtime() -> None:
    assert "中文对照；英文源文件" not in AGENT.instructions
    assert "## 行为准则" not in AGENT.instructions
    for skill in SERVICE_SKILL_REGISTRY.list():
        assert "中文对照；英文源文件" not in skill.content
        for reference in skill.references:
            assert "中文对照；英文源文件" not in reference.content
