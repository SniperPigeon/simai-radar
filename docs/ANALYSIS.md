# 谱面分析 MVP

核心按显式配置调用独立维度分析器，返回原始指标；当前只有用于验证流程的 HOLD 频率。评分层已默认接入按 feature 独立配置的 dummy Pn 映射器，用于打通 MVP；旧版六维与官方校准未迁移。

## 直接调用

```python
from mairadar.parser import parse_chart
from mairadar.analysis import ChartAnalyzer

parsed = parse_chart("(120){4}1h[4:1],E")
result = ChartAnalyzer().analyze(parsed)
assert result.features["hold"].data == 2.0
```

调用不需要歌曲 metadata、路径、音频或曲绘，不加载文件读写、评分模块或 GUI。输入沿用 ParseResult 的完整性和时间字段，保留 parser 的原文诊断。不会重新解析 Simai。

## 配置与扩展

默认配置位于 `src/mairadar/analysis/config.py`，也可以由调用方传入：

```python
from mairadar.analysis import ChartAnalyzer, FeatureResult
from mairadar.analysis.features import HoldFrequencyAnalyzer

features = {"hold": HoldFrequencyAnalyzer}
analyzer = ChartAnalyzer(features=features)
```

每个类实现 `analyze(context) -> FeatureResult`，且支持无参数构造。名字为字母开头的字母、数字、下划线组合。结果和 CSV 列按配置顺序输出；不限制为六维。类在每张谱面每个维度调用时重新构造，避免跨谱面残留状态。

AnalysisContext 提供事件元组、chart_end_time_s、last_event_end_s 和 duration_s。各维度应只读事件；调度器为每个维度复制输入快照，防止意外修改影响调用方或其他维度。MVP 不预先建立窗口索引或几何缓存。

FeatureResult 仅包含 data 和 success 两个字段，不携带单位、中间统计量或诊断。成功时 data 为有限数值；失败时 data=None、success=False。维度抛出的异常由主分析器记录到 AnalysisResult.diagnostics，其他维度仍执行；汇总状态为 ok、partial 或 error。

## HOLD 示例口径

```text
hold_raw = 普通 Hold 声明数 / duration_s
duration_s = max(chart_end_time_s, last_event_end_s 或 0)
```

输出为一个数值。仅统计 kind=hold，排除 TouchHold；同位置同时声明不去重，长 Hold 只计一次。时长保留开头休止、结尾空槽及超出谱面结束标记的持续物件尾部，不叠加音频 offset。

正常完整且时长为正的无 Hold 谱面得到 FeatureResult(0, success=True)；零时长返回 FeatureResult(None, success=False)。解析不完整或模型校验失败时，各维结果均标记失败，主分析器分别记录 PARSE_INCOMPLETE 或 INVALID_INPUT。

## 统一 CLI 与模式

统一入口为 `scripts/mairadar.py`，安装包后使用 `mairadar`，或设置 PYTHONPATH=src 后使用 `python -m mairadar`。三种模式由外围 pipeline 组合：

| --mode | 输入 | 执行步骤 | 输出 |
| --- | --- | --- | --- |
| full | 原始 Simai 文件或目录 | 解析 → 特征分析 → 映射 → 导出 | 总表 CSV 与曲绘 |
| analysis | 已导出的 bundle 根目录 | 特征分析 | 终端逐行 JSON，不写文件 |
| analysis_score | 已导出的 bundle 根目录 | 特征分析 → 映射 → 导出 | 总表 CSV 与曲绘 |

```bash
python scripts/mairadar.py --mode analysis --input data/parsed
python scripts/mairadar.py --mode analysis --choose
# 以下模式默认使用 dummy Pn 映射器：
python scripts/mairadar.py --mode analysis_score --input data/parsed --output outputs/scored
python scripts/mairadar.py --mode full --input data/raw --output outputs/full --difficulty 5 6
```

需要 Python 3.11+。`--choose` 延迟加载 tkinter 打开输入目录选择器；没有 tkinter 或 GUI 时使用 `--input`。取消选择返回非零。`--difficulty` 和 `--chart-type` 仅供 full 使用。analysis 不接受 --output，另外两种模式必须提供 --output。

full 对目录递归发现 maidata.txt、majdata.txt 和 .simai；解析一次后直接把内存事件交给分析器，不导出或重新读取中间 bundle。单个文件内的不同难度分别输出报告行。未知类型只由显式 metadata 或 --chart-type 提供。

只保留统一 CLI；纯解析和事件 bundle 导出通过 parse_file / write_bundle 库 API 调用。

analysis 和 analysis_score 将根目录的每个直接子文件夹作为现有 CSV bundle 读取，不递归，也不自动发现 maidata。直接子文件忽略。损坏、缺少文件或不完整的 bundle 均保留结果行；其他目录继续处理。空输入、批量部分失败、映射失败和导出失败均返回非零。

analysis 的终端 JSON 每张谱面一行，包含 metadata、`analysis.features` 下各维的 data/success 和诊断；既可直接检查，也可由外部调用者消费。摘要和错误提示不混入 JSON 行。

## 总表导出

输出目录必须是新目录或空目录，且 pipeline 不允许输入输出互相包含。导出先写临时目录再发布，不覆盖已有报告或用户文件。

```text
outputs/analysis/
  charts.csv
  covers/
    曲名-5-dx.png
```

CSV 使用 UTF-8 BOM，固定列为 title、artist、designer、difficulty_index、level、chart_type、cover_path、status、diagnostics，后面追加配置中的 `<feature>_raw`。失败值留空，diagnostics 为 JSON，包含源子目录、解析和主分析器诊断、各维成功与否。metadata 缺失时留空，不猜测歌曲属性或类型。

曲绘按原样复制，cover_path 相对 CSV 所在目录。曲绘缺失不影响原始指标；复制失败或文件名冲突时，相关行保留指标、曲绘引用留空、状态标记失败或部分成功。文件名采用现有的标题、难度编号和显式 DX/SD 约定，不追加 hash，也不去重报告行。

批处理与导出可以分别使用：

```python
from mairadar.batch import analyze_directory
from mairadar.exporters import CsvExporter

batch = analyze_directory("data/parsed", analyzer=analyzer)
report = CsvExporter().export(
    batch.records, "outputs/analysis", feature_names=batch.feature_names,
)
exit_code = max(batch.exit_code, report.exit_code)
```

CsvExporter 接收 AnalysisRecord 序列，不发现目录、不运行分析器。AnalysisRecord 在外围组合 metadata、AnalysisResult 和可选曲绘路径；Play 可只取内存结果，或自行组装记录交给导出器。批次逐张处理事件，汇总时仅保留指标和报告信息。

## 按 feature 配置评分映射

`ScoreTransformer.transform(AnalysisResult) -> ScoreResult` 保留为整体变换接口。默认实现 FeatureScoreTransformer 按 feature 名称分发，每个 feature 可以使用不同的映射器类和参数。子映射器只需实现 `map(data: float) -> float`，返回标准分数。

默认配置位于 `src/mairadar/scoring/config.py`：

```python
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    # 增加 feature 时在此声明它自己的映射器实例与参数。
}
TRANSFORMER = FeatureScoreTransformer
```

p50、p100 是原始指标的数值阈值，要求 `0 < p50 < p100` 且均为有限数值。默认 HOLD 的 1、2 仅是 dummy 参数，不是从官方谱面计算的百分位。不会根据本批次重新拟合阈值。

DummyPnMapper 使用两段线性变换：

```text
x <= 0:           0
0 < x <= p50:     50 * x / p50
p50 < x < p100:   50 + 150 * (x - p50) / (p100 - p50)
x >= p100:        200
```

即 0→0、P50→50、P100→200，范围外截断到 0–200，保留浮点分数、不取整。默认 HOLD 示例：0.5→25、1→50、1.5→125、2→200。NaN、无穷值及无效阈值明确报错。

同一映射器类可以配置不同阈值，也可以替换为其他实现 map 的类。调用方可以直接注入自己的配置：

```python
from mairadar.pipeline import run_pipeline
from mairadar.scoring import DummyPnMapper, FeatureScoreTransformer

mapper = FeatureScoreTransformer(
    {"hold": DummyPnMapper(p50=2.0, p100=6.0)},
    mapping_version="my-hold-v1",
)
batch, report = run_pipeline(
    "analysis_score", "data/parsed", output="outputs/scored", transformer=mapper,
)
exit_code = max(batch.exit_code, report.exit_code)
```

原始 feature 失败时不调用其映射器，标准分数留空。缺少某个 feature 的配置，或它的映射器报错、返回非有限值时，只将该 feature 标为失败，其他 feature 继续映射，原始数据保留；批次返回非零。多余配置允许存在，便于分析器选择特征子集。

ScoreResult 独立存储标准分数与 mapping_version。默认版本为 dummy-pn-v1；后续调整指标或参数时应同步维护版本。pipeline 校验映射输出维度与特征配置一致，禁止为失败的原始 feature 生成成功分数。若手动将 TRANSFORMER 设为 None，映射模式仍会明确报错；analysis 不需要评分配置。

pipeline 将评分输出附在 AnalysisRecord.scores 上，导出器追加 `<feature>_score` 并保留映射诊断和版本。映射全部失败时仍保留分数列，以空值表示失败。直接调用导出组件输出原始分析时，可省略评分结果及标准分数列。

## 验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

合成测试覆盖已知频率、重复声明、持续物件尾部、变速、无效时长、维度失败隔离、批量部分失败、曲绘相对路径、冲突及发布失败。模式测试注入仅用于测试的映射器，验证 full 与 analysis_score 等价、analysis 不映射不导出、full 不读写中间 bundle，以及映射失败继续处理。另用自定义分析器和内存导出替身验证各层可替换。默认 dummy Pn 另有锚点、区间插值、截断、独立参数和错误隔离测试，并通过真实 CLI 对合成输入运行 full / analysis_score 验证完整输出；不代表已完成官方校准。文件夹选择的选择、取消、不可用分支通过 mock 验证，不代表已进行原生窗口人工验收。
