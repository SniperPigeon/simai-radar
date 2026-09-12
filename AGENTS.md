# simai-radar 工作约定

- 这是独立 Python 项目，与原 fyp 工作区无业务关系。
- 首次进入此工作区先阅读 `HANDOFF.md`、`docs/SCHEMA_PROPOSAL.md`、`docs/SYNTAX_SUPPORT.md`。HANDOFF 是一次性快照，之后以用户新消息和正式设计文档为准，不持续追加对话流水。
- 创建骨架时事件 schema 尚待用户确认。只有在没有后续确认记录时才保持 prototype 阶段；一旦用户已确认，不重复索要同一确认。
- 解析器只负责语义、时间和诊断；分析、标准化、标定和映射放在独立模块/脚本。不要把 baseline 或难度等级权重放进解析器。
- 不输出面向用户的语法树清单；维护 `docs/SYNTAX_SUPPORT.md` 的实际支持状态。未知语法明确诊断，不静默丢物件或自动猜节奏。
- 主要语义参考为 MajdataPlay 的实际 submodule pin 和播放器扩展；MajSimai README 只是辅助，实际代码及行为样例优先。固定参考版本，不凭日期标签断言兼容。
- 保留原始文本位置、统一时间原点、事件唯一身份。小节/窗口是事件表的视图，不复制跨界物件。
- 真实输入和生成结果放 `data/` / `outputs/`，提交 `res/` 中小型合成样例和配置；不要意外导入曲库、音频、曲绘或临时缓存。
- 保留已有 Git remote、LICENSE 和用户改动。上游源码保持外部参考；此骨架没有 vendored dependency。
- 当前不主动 commit、push、创建 PR 或运行完整曲库重算，除非后续任务需要或用户另行要求。
