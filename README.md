# simai-radar

Python Simai 谱面解析与特征分析项目。先把文本解析成可审计的事件时间轴，再由独立脚本计算 raw 特征与分数映射。

**当前阶段：目录骨架和事件模型 prototype。Slide 内联路径与连接段子数组已确认，其余事件字段尚待定稿；解析器、分析器和实际映射均未实现。**

换工作区后先读 [HANDOFF.md](HANDOFF.md)，再读 [事件字段草案](docs/SCHEMA_PROPOSAL.md) 和 [语法范围](docs/SYNTAX_SUPPORT.md)。handoff 是 2026-09-12 的一次性上下文快照，不是持续追加的日志。

```text
src/simai_radar/
  model/       事件、时间轴、诊断的数据模型（待实现）
  parser/      metadata、时序、物件语法与方言兼容（待实现）
  io/          CSV bundle 读写及可选 DataFrame 适配（待实现）
  analysis/    小节、窗口、分布、六维 raw 指标（待实现）
  scoring/     标准化、标定、raw → score 映射（待实现）
scripts/       各阶段独立命令入口；当前只有 map_scores.py 占位
res/
  examples/schema_v0.1_draft/  单谱子目录中的人工 CSV，非解析器输出
  config/                    映射配置草案
  reference/                 上游 commit 与链接，不包含第三方源码
tests/         按模块预留测试目录；fixtures/ 保存自造回归谱面
data/
  raw/         本地输入
  parsed/      事件时间轴导出，拟按每张难度谱面一个子目录
  features/    原始分析指标
  scores/      映射后的分数
outputs/       报告和图形
docs/          架构、字段、语法、映射说明
```

小型示例和配置放 `res/` 并提交；真实曲库及生成产物放 `data/`、`outputs/`，默认不提交。包不绑定 Unity；核心解析和 CSV 读写计划使用 Python 标准库，DataFrame 是同一模型的便捷视图，而不是唯一存储格式。

开发环境约定为 Python 3.11+。创建骨架时可用解释器为 `/opt/anaconda3/bin/python3`（3.13.9）；`/usr/bin/python3` 为 3.9.6，低于本项目约定。后续工作区应自行确认解释器，不把此绝对路径写入运行时代码。

可运行的占位命令：

```bash
python scripts/map_scores.py --help
```

该脚本目前只展示规划中的参数，实际执行会明确返回非零状态。不要把它当成已完成的评分器。当前没有解析命令、已安装依赖、锁文件、自动生成数据或通过的解析测试。

主要参考：MajdataPlay 实际锁定的 MajSimai + MajdataPlay 自身扩展；版本见 [res/reference/upstreams.json](res/reference/upstreams.json)。当前 Git 仓库已有 MIT LICENSE，本次只新增本项目自己的文档、样例与骨架，未引入上游源码。
