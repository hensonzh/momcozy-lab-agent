# Momcozy AI 提示词与 Skill 中文对照

本目录只供产品、医疗和工程团队阅读与审阅，**不参与智能体运行时加载**。运行时唯一的英文源文件位于 `agent/app/agent/`（相对于本仓库为 `app/agent/`）。中文文件采用与英文文件相同的相对路径；`name`、reference ID、工具名称与交叉引用的文件名保持英文标识符，以便逐项核对。

## 同步维护约定

- 每次修改 `app/agent/system_prompt.md`、任一 `app/agent/skills/**/SKILL.md` 或其 `references/*.md` 时，在同一次变更中同步修订本目录下相同相对路径的中文版本；反向修改中文文案时，也先确认英文运行时版本是否需要同步。
- 中文文件第一行的 SHA-256 是对应英文源文件原始字节的摘要。英文源文件变更后，应先审查并更新译文，再更新摘要；摘要通过不代表语义翻译经过自动验证，仍需人工审阅。新增或删除 Skill/reference 时同步新增或删除中文镜像。
- 英文文件为运行时权威版本。不要把本目录加入 Skill registry 或系统提示词拼接路径，也不要把中文镜像写入模型的 system/developer 消息。
- 校验：在 `agent/` 目录运行 `.venv/bin/python -m pytest -q tests/test_agent_chinese_mirrors.py tests/test_agent_package_structure.py`。完整测试集也包含该校验。

## 文件对照

- [`system_prompt.md`](system_prompt.md) ↔ `app/agent/system_prompt.md`
- [`skills/lactation/SKILL.md`](skills/lactation/SKILL.md) ↔ `app/agent/skills/lactation/SKILL.md`
- [`skills/lactation/references/`](skills/lactation/references/) ↔ `app/agent/skills/lactation/references/`
