# 轻量谱面分析器与评分映射计划

状态：分析接口、HOLD 示例、batch、CSV/曲绘导出和统一 CLI 已实现；模式为 full、
parse_only、analysis、analysis_score。映射调用链已默认启用 dummy Pn，支持每个 feature
独立配置映射器及参数；官方校准尚未进行。调用方式与实际口径见
[分析说明](ANALYSIS.md)。0830 六维代码只作历史参考，暂不迁移其指标、权重或评分策略。

## 数据流

```text
根目录的直接子文件夹 → read_bundle → 内存分析输入
                                        ↓
                              ChartAnalyzer（核心调度）
                                        ↓
                              配置的独立 FeatureAnalyzer
                                        ↓
                              AnalysisResult（原始指标）
                                        ↓
                         可选 ScoreTransformer（默认 dummy Pn）
                                        ↓
                              ScoreResult（标准分数）

metadata + 原始指标 + 可选标准分数 + 外围曲绘引用 → 总表导出器
```

一个子文件夹作为一个已导出的谱面 bundle，总表一行对应一张谱面。目录读取、批处理、文件夹选择与导出均在外围；核心分析可直接接收 ParseResult 对应的内存数据，便于 Play 调用。metadata 由调用方管理，曲绘不进入核心分析参数。

## 核心分析接口

使用代码显式配置 `FeatureName: 分析器类`，按配置顺序收集结果和生成导出列。MVP 仅配置 `{"hold": HoldFrequencyAnalyzer}`，不创建六个空实现或动态插件发现机制。

拟定接口：

```python
FeatureAnalyzer.analyze(context: AnalysisContext) -> FeatureResult
ChartAnalyzer.analyze(parsed: ParseResult) -> AnalysisResult
ScoreTransformer.transform(result: AnalysisResult) -> ScoreResult
```

- AnalysisContext 提供事件、时间边界和完整性信息；公共计算仅按实际需要增加。
- FeatureResult 仅包含 data 和 success，不携带单位、中间统计量或诊断；异常由主分析器统一记录。
- AnalysisResult 以配置中的 FeatureName 为键收集 FeatureResult，并保留解析和分析状态。
- 子分析器独立计算，不依赖其他子分析器的执行顺序，不重新解析原始 Simai。
- ScoreResult 单独保存标准分数、状态和映射配置版本，不覆盖原始指标。

以上签名为设计约定，具体类型落地时可做小幅调整。

## HOLD 示例维度

HOLD 仅用于验证 MVP 的配置、调度、批处理和导出，不代表最终维度定义。

暂定口径：仅统计 `kind == "hold"` 的事件，TouchHold 不计入；每个声明计一次，同时同位置不去重，持续时间不增加计数。

```text
hold_frequency = hold_count / duration_s
duration_s = max(chart_end_time_s, last_event_end_s)
```

时长从谱面时间原点计算，保留开头休止和可确定的尾部时间，不使用音频长度或重复叠加 offset。正常完整谱面无 HOLD 时为 0；时长为 0 或无法确定时 data=None、success=False。MVP 对不完整解析结果不生成有效指标，保留失败行与原因；以后有需要再引入按维度判断可计算性的机制。

## Pn 评分变换接口

评分层消费核心分析器输出的原始数值，按维度转换成标准分数。MVP 已实现 DummyPnMapper，由 FeatureScoreTransformer 按 feature 独立调度，配置为 FeatureName → 映射器实例，各实例有自己的参数。

未来 Pn 变换器使用人工调整后固化在代码配置中的官方谱面参考阈值。每个维度独立配置若干锚点，每个锚点包含百分位标签 Pn、原始指标阈值和目标标准分数；原始阈值才是插值输入坐标，百分位标签用于说明其来源。

配置注入变换器，运行时不根据当前批次重新计算百分位，不查询或维护官方歌曲注册表。指标版本与映射配置版本需匹配；指标定义改变后，旧阈值不能自动沿用。

DummyPnMapper 将 0–P50 线性映射到 0–50，将 P50–P100 线性映射到 50–200，范围外截断到 0–200，不取整。要求原始阈值满足 0<P50<P100；默认 HOLD 暂设 P50=1、P100=2，仅用于 MVP。缺少单个维度映射或该映射失败时只留空对应分数，不阻断其他维度。

## 总表与曲绘

```text
outputs/analysis/
  charts.csv
  covers/
    曲名.png
```

固定列为 title、artist、designer、difficulty_index、level、chart_type、cover_path、status、diagnostics。动态列按配置生成 `<feature>_raw`；映射模式追加 `<feature>_score`。MVP 原始列为 hold_raw，直接使用 FeatureResult.data；映射模式另有 hold_score，失败值留空。

cover_path 相对 CSV 所在目录，例如 `covers/曲名.png`。导出组件按来源、标题和曲师作单次报告内的临时归组，同曲全部难度与 DX/SD 共用一份曲绘；缺图留空，同名曲绘内容相同则复用，内容不同明确诊断且不覆盖，不追加 hash。该规则不构成歌曲身份或注册表。DX/SD 优先使用显式值；缺失时在语法解析后按约定的 DX 物件规则补全，不从曲名猜测。

单个 bundle 或维度失败时继续处理其他项，失败数值留空并记录诊断；批量部分失败返回非零状态。原始指标和标准分数的有效状态分别保留，映射失败不覆盖已得到的原始值。

## 实施顺序与验收

1. 定义分析输入、FeatureAnalyzer、结果模型与显式配置；提供 ScoreTransformer 接口和按 feature 独立配置的 dummy Pn 实现。
2. 实现 HOLD 示例维度，打通直接内存调用。
3. 接入目录批处理与总表、曲绘导出；CLI 和轻量文件夹选择入口共用 batch API。
4. 用合成 bundle 验证已知 HOLD 频率、零时长、重复声明、跨窗口持续物件、不同配置下的列顺序，以及部分失败仍继续处理且返回非零。
5. 验证导出后 CSV 相对曲绘路径有效；确认调用核心不需要文件系统、metadata 或评分配置。

本轮只实现 dummy Pn，不迁移旧六维、不拟合官方 baseline，不运行完整曲库重算。后续指标与评分规则由新的设计逐项加入。
