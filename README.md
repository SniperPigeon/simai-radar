# simai-radar

simai-radar 是一个面向 maimai 谱面雷达图评分研究的数据分析 codebase。项目将**谱面解析与特征分析**、**原始特征到标准化分数的映射**、**数据导出**拆成彼此独立的层，既方便离线批量实验，也为未来接入 MajdataPlay、提供实时雷达图分析保留了纯内存调用路径。

当前版本已经打通完整管线，但还没有定义最终雷达维度，也没有完成官方数据校准。仓库内置的 `hold` 特征和 dummy Pn 映射只用于演示、测试接口与验证数据流，不应被当作正式评分标准。

## 管线如何构成

```text
原始 inote / maidata
        │
        ▼
parser：Simai 文本 → 统一事件时间轴 + 解析诊断
        │
        ▼
analysis：事件时间轴 → 各维独立的原始特征值
        │
        ▼
scoring：原始特征值 → 标准化雷达分数
        │
        ▼
exporter：谱面 metadata + 原始值 + 标准分 → charts.csv / 曲绘
```

各层只依赖上一层的结构化结果：

- `mairadar.parser` 只处理文本语义、时间和诊断，不读取文件，也不负责歌曲管理。
- `mairadar.analysis` 只读取解析后的事件，不重新扫描原始 Simai。
- `mairadar.scoring` 只映射原始特征，不参与特征计算，也不会按当前批次偷偷重拟合阈值。
- `mairadar.exporters` 只消费组合好的分析记录，不发现文件、不运行解析器或分析器。
- `mairadar.pipeline` 负责按使用场景组装以上各层，并允许调用方替换分析器、映射器和导出器。

面向 MajdataPlay 的实时接入可以走纯内存路径：播放器提供 inote 文本及歌曲 metadata，依次调用 parser、analyzer 和 mapper，再把结果交给 UI。文件发现、CSV bundle 和曲绘复制都不是实时分析的必需依赖。

## 快速开始

需要 Python 3.11 或更高版本。核心运行时仅使用标准库。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

用仓库内的合成谱面跑通“解析 → 分析 → 映射 → 导出”：

```bash
mairadar \
  --mode full \
  --input "res/examples/schema_v0.3/Schema Prototype-5-sd/maidata.txt" \
  --output outputs/demo
```

结果位于 `outputs/demo/charts.csv`。输出目录必须是不存在的新目录或空目录；已有报告不会被覆盖。

如果不想安装包，也可以直接使用仓库脚本：

```bash
python scripts/mairadar.py --mode full --input data/raw --output outputs/full
```

### 四种运行模式

| 模式 | 输入 | 执行内容 | 输出 |
| --- | --- | --- | --- |
| `full` | 单个原始 Simai 文件，或原始谱面目录 | 解析 → 特征分析 → 映射 → 导出 | 汇总 `charts.csv` 与曲绘 |
| `parse_only` | 单个原始 Simai 文件，或原始谱面目录 | 仅解析并保存 | 每张谱面一个事件 bundle |
| `analysis` | 事件 bundle 根目录 | 特征分析 | 每张谱面一行 JSON；不写文件 |
| `analysis_score` | 事件 bundle 根目录 | 特征分析 → 映射 → 导出 | 汇总 `charts.csv` 与曲绘 |

```bash
# 只解析原始谱面并保存事件 bundle
mairadar --mode parse_only --input data/raw --output data/parsed

# 只分析已经保存的事件 bundle
mairadar --mode analysis --input data/parsed

# 分析并映射为标准分
mairadar --mode analysis_score --input data/parsed --output outputs/scored

# 只处理 maidata 中的 5、6 号难度，并显式指定谱面类型
mairadar --mode full --input data/raw --output outputs/full --difficulty 5 6 --chart-type dx
```

`full` 和 `parse_only` 会递归发现 `maidata.txt`、`majdata.txt` 和 `.simai`。
`full` 只解析一次并在内存中继续分析，不会先生成中间 bundle；`parse_only` 到写入事件
bundle 为止，不构造分析器或映射器。`--difficulty` 与 `--chart-type` 仅用于这两个原始输入
模式；DX/SD 只读取明确 metadata、调用方参数或独立的解析后检测，不会根据曲名猜类型。

批处理中一张谱面失败不会阻止其余谱面继续处理。只要存在解析不完整、分析/映射失败、bundle 损坏或导出失败，进程就会返回非零状态，并把细节保留在诊断中。

## 解析器 API

### 解析单张 inote

```python
from mairadar.parser import parse_chart

parsed = parse_chart("(120){4}1,1?-5[4:1],E")

print(parsed.events)
print(parsed.diagnostics)
print(parsed.complete)
```

`parse_chart(text)` 返回 `ParseResult`，包含：

- `events`：按动作开始时间稳定排序的事件列表；
- `diagnostics`：带原文位置和恢复策略的解析诊断；
- `complete`：解析结果是否完整；
- `chart_end_time_s`：文本时间轴结束位置；
- `last_event_end_s`：最后一个非 timing 事件的结束位置。

这个入口不需要曲名、难度、DX/SD、路径或文件 hash。

### 解析完整 maidata

```python
from pathlib import Path
from mairadar.parser import parse_text

text = Path("data/raw/song/maidata.txt").read_text(encoding="utf-8-sig")
bundles = parse_text(text, difficulties=[5, 6])
```

`parse_text` 返回 `list[ChartBundle]`，每个难度由一个 `Chart` metadata 对象、事件列表、诊断和完整性状态组成。若希望由库负责 UTF-8 BOM 文件读取，可使用：

```python
from mairadar.io import parse_file

bundles = parse_file("data/raw/song/maidata.txt", difficulties=[5, 6])
```

## 事件格式

当前交换格式版本为 `events-0.3`。`Event` 定义在 `src/mairadar/model.py`，写入 bundle 后对应 `events.csv` 的 22 列。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `event_id` | `int` | 单次解析结果内从 1 连续编号；包含 BPM 事件 |
| `kind` | `str` | `tap`、`hold`、`touch`、`touch_hold`、`slide` 或 `timing` |
| `is_slide_head` | `bool?` | 该 Tap 是否为 Slide 头；timing 留空 |
| `timing_type` | `str?` | timing 子类型；当前为 `bpm` |
| `slide_declare_time_s` | `float?` | Slide 的声明时间；有头和无头 Slide 都保留 |
| `start_time_s` | `float` | 动作开始时间；Slide 为实际开始滑动的时间 |
| `end_time_s` | `float` | 动作结束时间；瞬时事件与开始时间相同 |
| `start_beat` | `str?` | 全局四分音符拍轴上的开始位置，以约分后的有理数字符串保存 |
| `end_beat` | `str?` | 全局四分音符拍轴上的结束位置 |
| `bpm` | `float?` | BPM timing 事件声明的新值 |
| `position` | `str?` | 外圈 `1`–`8`，或 `A1`、`B1`、`C` 等 Touch 位置 |
| `head_event_id` | `int?` | Slide 所引用的头事件 ID；无头 Slide 留空 |
| `slide_path_json` | `list[dict]?` | Slide 的有序路径段；连接 Slide 仍只占一行 |
| `is_break` | `bool?` | 该事件自身是否为 BREAK |
| `is_ex` | `bool?` | 该事件自身是否为 EX |
| `is_mine` | `bool?` | 该事件自身是否为 mine |
| `flags_json` | `dict?` | 额外修饰符，如无头标记、`tap_head`、`using_sv` |
| `raw_token` | `str` | 产生该事件的原始 token |
| `source_start` | `int` | 原文中的 0-based Unicode code point 起点 |
| `source_end` | `int` | 原文中的半开终点 |
| `source_line` | `int` | 1-based 原文行号 |
| `source_column` | `int` | 1-based Unicode code point 列号 |

Slide 路径段内联在 `slide_path_json` 中：

```json
{
  "shape": "-",
  "start_position": "1",
  "via_position": null,
  "end_position": "5",
  "bar_count": 20,
  "start_time_s": 1.5,
  "end_time_s": 2.0,
  "raw_segment": "-5[4:1]"
}
```

普通 Slide 会生成一个头 Tap 和一个路径事件；同头多路径只生成一个头，各路径分别成为事件并共享 `head_event_id`。无头 Slide 不生成虚拟头，但仍保存声明时间。事件排序完成后才重新连续编号，并同步更新头引用；ID 只在本次结果中有效。

`bar_count` 是固定 MajdataPlay prefab 的长度单位，不是物理距离。连接 Slide 的总持续时间按各段 `bar_count` 比例分配；所有当前支持的标准形状都会得到连续、覆盖整条路径的逐段时间。

同一时间、同一位置的重复声明不会去重。所有秒时间都以谱面第一槽为原点，保留开头休止但不包含音频 offset；`Chart.offset_s` 由调用方在边界处应用一次。未知语法会产生明确诊断，不会静默吞掉物件或猜测无法确定的节奏。

完整的字段约束、Slide 细节和当前语法边界见 [事件格式说明](docs/SCHEMA_PROPOSAL.md) 与 [语法支持状态](docs/SYNTAX_SUPPORT.md)。

### test0913 谱包修正

`scripts/repair_test0913_charts.py` 是独立的一次性转写修正脚本，默认预览，仅处理已审阅的文件、难度和错误 token；不在 parser 内自动修谱。使用 Python 3.11 或更新版本：

```bash
python3 scripts/repair_test0913_charts.py
python3 scripts/repair_test0913_charts.py --apply
```

默认输入根目录为 `data/raw`（包含 `AstroDX-raw`），可用 `--root` 指定；原始字节备份保存在 `outputs/test0913-repairs/originals`，可用 `--backup-root` 指定输入树外的目录。脚本输出逐项 JSON 日志，保留 BOM 和原有换行；第二次执行不再修改文件，已有备份不覆盖。

修正规则包括对径 v 改直线、V 改为距原拐点最近且终点合法的两格拐点、缺失 h 补全、Oboro 的时长移到 Slide 一侧、混合 Slide 时长按有理数相加并合并到末尾、删除多余冒号及已确认杂字符、C8/C8f 改 C。未列入规则的错误继续诊断，不自动猜测曲目 metadata 或选择重复正文。

脚本也删除已确认的 `1$/` 尾部空同时押成员，保留用于休止的逗号及同位置重复物件。批处理跳过空谱，含实质语法错误的谱面仍报告失败。

后续确认的规则也已纳入：7处缺失同时押分隔符补 `/`；仅对 INTERNET YAMERO 保留第一份 `inote_6`，仅对 ∀ 保留 `des_4=Luxizhel`。这些是逐文件的明确选择，不改变 parser 对其他冲突重复字段的诊断策略。

## 保存和读取事件 bundle

事件 bundle 是解析层与离线分析层之间的可校验交换格式，不是歌曲数据库。每张谱面占一个子目录：

```text
data/parsed/
  <title>-<difficulty_index>-<dx|sd>/
    manifest.json
    charts.csv
    events.csv
    diagnostics.csv
    cover.png          # 可选；保留原图格式
```

```python
from mairadar.io import parse_file, read_bundle, write_bundle

for bundle in parse_file("data/raw/song/maidata.txt", difficulties=[5, 6]):
    directory = write_bundle(
        bundle,
        "data/parsed",
        cover_path="data/raw/song/bg.png",  # 可省略
    )

restored = read_bundle(directory)
```

`charts.csv` 保存一行谱面 metadata，包括标题、作者、谱师、难度编号、等级、DX/SD、offset 和时间边界；未知 maidata metadata 保留在 `metadata_json`。`diagnostics.csv` 保存解析级别、代码、消息、原文范围和恢复策略。

`manifest.json` 记录 schema/parser 版本、固定上游 commit、表名、行数、完整性状态、可选曲绘路径以及各文件 SHA-256。`read_bundle` 会先校验布局、checksum 和模型约束。checksum 只用于文件完整性，不是歌曲身份；项目不会创建全局 ID、身份 hash、曲库注册表或去重索引。

目录名使用调用方提供的标题、难度编号和 DX/SD 类型；缺失项保留空段（如 `曲名-5-`、全部缺失时 `--`），metadata 本身保持为空，不从文件名补曲名。非法文件名字符替换为下划线，不自动追加 hash 后缀。默认拒绝覆盖同名 bundle；即使显式使用 `overwrite=True`，也只会替换布局匹配且不含额外用户文件的旧 bundle。

事件 bundle 与最终分析报告不是同一种产物。评分模式导出的报告结构是：

```text
outputs/scored/
  charts.csv
  covers/
    <title>-<difficulty_index>-<dx|sd>.png
```

报告表先保存固定 metadata 与状态列，再按分析配置顺序追加 `<feature>_raw` 和 `<feature>_score`。`diagnostics` 列为 JSON，汇总输入、parser、analysis 和 scoring 各层诊断；`cover_path` 始终使用相对路径。

### 导出静态 visualizer

评分模式可以改用仓库内置的 MAI RADAR 静态模板，直接生成可浏览的独立目录：

```bash
mairadar \
  --mode full \
  --input data/raw \
  --output outputs/visualizer \
  --format visualizer
```

产物包含 `index.html`、`app.js`、`styles.css`、`data/songs.json` 和
`assets/covers/`。页面通过 `fetch` 读取 JSON，因此需在输出目录启动本地 HTTP
服务器，而不是直接双击 HTML：

```bash
cd outputs/visualizer
python3 -m http.server 4173 --bind 127.0.0.1
```

visualizer exporter 与 CSV exporter 并列，只消费已经完成的 `AnalysisRecord`；它不读取
原始 Simai、不运行 parser/analyzer，也不参与分数映射。模板固定保存在
`res/visualizer/`，CLI 不接受外部模板参数。维度由当前分析配置顺序生成，未知维度使用
可读的字段名和循环配色；需要自定义显示名时，可在代码调用中传入展示配置：

```python
from mairadar.exporters import DimensionPresentation, VisualizerExporter

exporter = VisualizerExporter({
    "note_density": DimensionPresentation("整体物量", "物量", "#ef476f"),
})
```

每次 JSON 内的歌曲和谱面使用 `song-1`、`chart-1` 形式的递增 ID，只在该次导出中
有效，不构成歌曲注册表或稳定身份。分组优先使用调用方保留的来源路径，并将 DX/SD
分开；不会用标题去重。难度筛选由实际数据动态生成。少于三个成功评分维度时页面仍可
显示分数明细、排行和 raw 分布，但不会绘制退化的雷达多边形。

## 添加自定义特征分析器

一个特征分析器是支持无参数构造、并实现 `analyze(context) -> FeatureResult` 的类。分析器只读 `AnalysisContext` 中的事件快照，不需要接触文件或原始 Simai。

```python
from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import NoteDensityAnalyzer


analyzer = ChartAnalyzer({"note_density": NoteDensityAnalyzer})
```

内置 `NoteDensityAnalyzer` 使用 1.5 秒固定窗口、Hold/Slide/Touch 物量补正和波动修正；
完整公式、Touch 邻接与边界规则见 [分析说明](docs/ANALYSIS.md#整体物量口径)。默认配置以
`note` 启用它，并使用当前观察批次的中位数与 P99 作临时映射锚点；该映射不代表正式校准。

`AnalysisContext` 提供：

- `events`：不可变事件元组；调度器会为每个维度提供独立快照；
- `chart_end_time_s` 与 `last_event_end_s`；
- `duration_s`：两者的有效最大值。

成功结果必须是有限数值 `FeatureResult(value)`；无法计算时返回 `FeatureResult(None, success=False)`。某个维度抛出异常只会将该维标为失败，其余维度仍会继续。当前结果契约是一维一个标量；单位和中间统计量应由特征实现或实验代码自行管理。

要让默认 CLI 长期启用一个特征，在 `src/mairadar/analysis/config.py` 中导入类并加入 `FEATURES`。字典键也是导出的列名前缀，必须以字母开头，且只能包含字母、数字和下划线；字典顺序决定输出顺序。

```python
FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
}
```

如果特征只属于某次实验，无需修改全局配置，直接构造 `ChartAnalyzer(features=...)` 并注入 pipeline 即可。

## 添加自定义分数映射器

每个特征映射器只需实现 `map(data: float) -> float`。默认的 `FeatureScoreTransformer` 按特征名分发映射器，并把结果组合成带 `mapping_version` 的 `ScoreResult`。

```python
from mairadar.scoring import FeatureScoreTransformer


class CappedLinearMapper:
    def __init__(self, scale: float):
        self.scale = scale

    def map(self, data: float) -> float:
        return min(200.0, max(0.0, data * self.scale))


transformer = FeatureScoreTransformer(
    {"note_density": CappedLinearMapper(scale=25.0)},
    mapping_version="note-density-v1",
)
```

映射输出必须是有限数值。原始特征失败时不会调用 mapper；缺少 mapper、mapper 抛出异常或返回非有限值时，只影响对应维度，原始值和其他维度仍会保留。

默认 CLI 从 `src/mairadar/scoring/config.py` 读取 `FEATURE_MAPPERS` 和 `TRANSFORMER`。新增默认特征时，应同时为它配置映射器；`TRANSFORMER` 必须是可以无参数构造的 class 或 factory。阈值或算法变化时也应更新 `mapping_version`，例如：

```python
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    "note_density": CappedLinearMapper(scale=25.0),
}


class RadarTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(FEATURE_MAPPERS, mapping_version="radar-v1")


TRANSFORMER = RadarTransformer
```

仓库提供的 `DummyPnMapper` 使用预先给定的 `p50`、`p100` 做两段线性映射：`0 → 0`、`P50 → 50`、`P100 → 200`，范围外截断；锚点可以由离线观察确定，但运行时不会从当前输入批次自动重新计算。

也可以完全替换整体 `ScoreTransformer.transform(AnalysisResult) -> ScoreResult`，或只在调用时注入实验配置：

```python
from mairadar.pipeline import run_pipeline

batch, report = run_pipeline(
    "full",
    "data/raw",
    output="outputs/experiment-v1",
    analyzer=analyzer,
    transformer=transformer,
)

exit_code = max(batch.exit_code, report.exit_code)
```

分析器、映射器和导出器都可以独立替换，因此新的特征定义、baseline/标准化实验和输出格式不需要进入 parser。

## 项目结构

```text
src/mairadar/
  parser/          Simai 文本、时间与物件语义
  analysis/        特征接口、调度器与特征实现
  scoring/         原始特征到标准分的映射
  exporters/       CSV 与静态 visualizer 导出
  model.py         事件、谱面 metadata 与解析结果
  io.py            原始文件适配及事件 bundle 读写
  batch.py         原始文件或 bundle 的批处理
  pipeline.py      full / parse_only / analysis / analysis_score 组合
  parse_export.py  原始谱面到事件 bundle 的独立批处理适配
  cli.py           命令行入口
res/               小型合成样例和固定参考
tests/             parser、analysis、scoring、I/O 与 pipeline 测试
data/              本地真实输入和中间产物，不提交曲库或媒体
outputs/           本地报告与验证产物
```

## 开发与验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

语义以 MajdataPlay 固定 submodule pin 的 MajSimai 源码和播放器行为为主，具体 commit 记录在 [`res/reference/upstreams.json`](res/reference/upstreams.json)。当前验证包括合成测试、CSV 往返、模型完整性检查和有限真实谱面抽样；`complete=true` 或 bundle checksum 通过不代表已经与游戏运行时完成独立差分验证。

更多设计与边界：

- [Parser 调用说明](docs/PARSER.md)
- [事件格式 events-0.3](docs/SCHEMA_PROPOSAL.md)
- [分析、映射与导出](docs/ANALYSIS.md)
- [架构说明](docs/ARCHITECTURE.md)
- [Simai 语法支持状态](docs/SYNTAX_SUPPORT.md)

## License

见 [LICENSE](LICENSE)。
