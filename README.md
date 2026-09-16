# simai-radar

simai-radar 是一个面向 maimai 谱面雷达图评分研究的数据分析 codebase。项目将**谱面解析与特征分析**、**原始特征到标准化分数的映射**、**数据导出**拆成彼此独立的层，既方便离线批量实验，也为未来接入 MajdataPlay、提供实时雷达图分析保留了纯内存调用路径。

当前版本已经打通完整管线，但还没有完成官方数据校准。`jack` 已替换早期的 Hold 频率
占位维度；`jack`、`sweep` 与 `slide_tricky` 暂时使用 identity 映射，便于先观察 raw 分布，其余临时
映射也不应被当作正式评分标准。

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
bundle 为止，不构造分析器或映射器。执行分析的 full、analysis 和 analysis_score 默认只
选择 5 号 Master 与 6 号 Re:Master；`--include-utage` 会在两者基础上追加 7 号宴谱，显式
`--difficulty` 则完全覆盖默认选择。parse_only 默认仍保存所有难度，也可显式筛选。
`--difficulty` 可用于所有模式，
`--chart-type` 仅用于两个原始输入模式；DX/SD 不会根据曲名猜类型。

批处理中一张谱面失败不会阻止其余谱面继续处理。只要存在解析不完整、分析/映射失败、bundle 损坏或导出失败，进程就会返回非零状态，并把细节保留在诊断中。

### 推荐的两阶段分析工作流

调试 analyser 或评分映射时，不需要每次重新解析原始 Simai。第一次先把原始谱面解析为
可校验的事件 bundle：

```bash
python scripts/mairadar.py \
  --mode parse_only \
  --input data/raw \
  --output data/parsed-v03
```

之后修改 analyser 或映射参数，可以直接读取这些 bundle，重新分析并生成 visualizer：

```bash
python scripts/mairadar.py \
  --mode analysis_score \
  --format visualizer \
  --input data/parsed-v03 \
  --output outputs/visualizer-new
```

在 visualizer 的“原始值分布”页调好各维 T1–T4 后，可以导出
`mapping_profile.json`。该文件冻结的是校准集 raw 锚点，而不是让新数据集重新计算百分位：

```bash
python scripts/mairadar.py \
  --mode analysis_score \
  --mapping-profile mapping_profile.json \
  --input data/test-parsed \
  --output outputs/test-scored
```

profile 将 T1/T2/T3/T4 映射到 50/100/150/200，并记录校准集最大 raw 值
`T4_max`。T4 到 T4_max 保持 200；开放集 raw 超过 T4_max 后，以 T3→T4
的斜率起步并渐近到 220，不会在新测试集上重新拟合。

若只想检查 analyser 的原始指标，不执行评分映射或生成报告：

```bash
python scripts/mairadar.py \
  --mode analysis \
  --input data/parsed-v03
```

`analysis` 与 `analysis_score` 不读取原始 Simai。只修改 Note、Peak 等分析算法或映射参数时，
可以持续复用同一批 bundle；修改 parser 或事件 schema 后才需要重新执行 `parse_only`。
现有旧版 `events-0.2` bundle 不能交给当前 `events-0.3` reader，需要从原始谱面重新生成。
报告输出必须是新目录或空目录。分析默认只选择 5 号 Master 与 6 号 Re:Master；需要在
默认集合上纳入 7 号宴谱时添加 `--include-utage`。

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

目录名使用调用方提供的标题、难度编号和 DX/SD 类型；缺失项保留空段（如 `曲名-5-`、全部缺失时 `--`），metadata 本身保持为空，不从文件名补曲名。非法文件名字符替换为下划线。默认拒绝覆盖同名 bundle；即使显式使用 `overwrite=True`，也只会替换布局匹配且不含额外用户文件的旧 bundle。

事件 bundle 与最终分析报告不是同一种产物。评分模式导出的报告结构是：

```text
outputs/scored/
  charts.csv
  covers/
    <title>.png
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
原始 Simai、不运行 parser/analyzer，也不在导出时修改分数。生成页面的分布工作台允许
用户按 raw 百分位在浏览器内试算当前维度的分数。模板固定保存在
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

`JackSequenceAnalyzer` 分键位寻找由 Tap/Hold 构成的快速纵连。相邻外键时间点不超过
八分音符；形成至少两个主键时间点后才允许飞键，每次飞键可包含同拍最多四个异键普通
Tap，允许分布在多个连续时间点；每次从首个异键到返回主键的跨度不超过八分音符。
回到主键后又要累计两个主键时间点才能再次飞出，下一次飞键重新获得独立的数量和时间
预算。主键物件权重为 1，打断 Tap 权重为 1.5；序列再按实际主键速度相对
180 BPM 等效十六分的 1.5 次方加权。候选按速度加权后的强度取前五，名次权重为
`1.3 × 0.645^(x−1)`；五条等强候选时 Top‑1 约占全部名次权重的 40%，后排按几何级数
快速衰减。完整边界规则见
[分析说明](docs/ANALYSIS.md#纵连口径)。

旧版 `SweepAnalyzer` 按时间批次用动态规划选择最长主干，并把同拍另外一至两个物件作为辅助
物件纳入；双押节点可以一键接收前段、由另一键发出后段，因此 `3,4,56,7,8` 一类换手扫键
不会在双押处截断。支持双扫、单扫/双扫互切、复合双押、折返、变速和长 Hold 占位跨键。基础门槛为十二分，
八分只允许作为减速后继；短 Hold 按 Tap，EX 声明为 0.3。变速比较使用 0.5% 相对容差；
速度相对 180 BPM 十六分取
平方根系数，连接倍率只作线性增量，不进行音符降权；单双宽度变化和双押接续不额外加权。
双押批次内物件局部乘 1.3。每个 family 以自身负荷除以自身持续时间；物理攻击
不超过 8 的 family 只从 Peak 候选排除，不另设时长门槛，最终取合格 family 中强度最高五组按
`1/sqrt(k)` 得到 Peak；所有 family density 的均值乘 `sqrt(chart_duration/150)` 得到 Mean，最终以
`0.6 × Mean + 0.4 × Peak` 混合。
识别只读取 parser 事件，不重新扫描 Simai；完整公式见
[分析说明](docs/ANALYSIS.md#扫键口径)。
另外提供双手位移 DP，可计算 family 内两手总位移、连续动作位移、空转移位和
自由手接管；空转位移以 150 BPM 八分音符内移动 2 个键距（10 键/秒）为参考施加速度压力，EX 只扣减
基础物量而不削弱接续奖励。当前默认 CLI 使用 `SweepBurstAnalyzer`，将速度基础物量和双手负荷放进
三个互不重叠的 2 秒窗口，按 `1/√k` 衰减后除以权重和。旧版 `SweepAnalyzer` 仍保留用于对照。
两组扫键若恰好隔半拍且单位速度相近，后组物理基础负荷局部增加 5%，不合并 family。

Slide 当前默认启用两个维度：`slide_tricky` 取五个最高单配置负荷，按 `1/log₂(k+1)`
作归一化加权平均（不足五项补零），每个配置
最多按 16 个逻辑干扰物件计；
Tricky 按全谱正等待时长的 0.05 秒众数桶建立基准，排除等待严格超过基准四倍的路径；
`slide_cumulate` 的独立 analyser 已重新加入默认分析和评分链路；当前
`data/mapping_profile.json` 可为它单独保存 GUI 调整后的锚点；
`slide_sequence` 只分析连续阵、
同拍双押和同头多路径；严格快于八分音符的 Slide 启动不进入星星阵主干，
但不打断前后正常间隔的阵列，纯快速流也不降采样成八分阵；恰好八分保留。
每段以 4 个启动点为长度基准，16 点达到 3 倍，之后开方放缓；全谱取最强五段，
按 `1/log₂(k+1)` 衰减并用固定五项权重和归一化。同拍额外 Slide 已计入并发项。
`slide_tricky` 的 Tap 干扰包含同位、扫键和实际 Slide 头修正，
Touch 连通组最多计两组，启动拍统一将物件负荷除以二，并只追加启动后一拍以内的运动
交互；普通 Tap/Hold 以 180 BPM 等效八分为中性点，更慢时按平方根下降，更快时线性提升
并在 1.5 封顶。每个外部物件只归属一个最近的相关配置。完整公式见
[分析说明](docs/ANALYSIS.md#slide-压力口径)。

`AnalysisContext` 提供：

- `events`：不可变事件元组；调度器会为每个维度提供独立快照；
- `chart_end_time_s` 与 `last_event_end_s`；
- `duration_s`：两者的有效最大值。

成功结果必须是有限数值 `FeatureResult(value)`；无法计算时返回 `FeatureResult(None, success=False)`。某个维度抛出异常只会将该维标为失败，其余维度仍会继续。当前结果契约是一维一个标量；单位和中间统计量应由特征实现或实验代码自行管理。

要让默认 CLI 长期启用一个特征，在 `src/mairadar/analysis/config.py` 中导入类并加入 `FEATURES`。字典键也是导出的列名前缀，必须以字母开头，且只能包含字母、数字和下划线；字典顺序决定输出顺序。

```python
FEATURES = {
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "sweep": SweepBurstAnalyzer,
    "slide_tricky": SlideTrickyAnalyzer,
    "slide_sequence": SlideSequenceAnalyzer,
    "jack": JackSequenceAnalyzer,
    "slide_cumulate": SlideCumulateAnalyzer,
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
    "jack": IdentityMapper(),
    "note_density": CappedLinearMapper(scale=25.0),
}


class RadarTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(FEATURE_MAPPERS, mapping_version="radar-v1")


TRANSFORMER = RadarTransformer
```

仓库提供的 `DummyPnMapper` 使用预先给定的 `p50`、`p100` 做两段线性映射：`0 → 0`、`P50 → 50`、`P100 → 200`，范围外截断；锚点可以由离线观察确定，但运行时不会从当前输入批次自动重新计算。`IdentityMapper` 只校验有限数值并原样传递。默认配置暂时对 `jack`、`sweep` 和 `slide_tricky` 使用 identity，避免在 GUI 查看 raw 分布之前先套用未校准锚点；其他维度仍使用各自的 dummy Pn。

GUI 分布页的百分位滑块始终从 `rawScores` 计算阈值。首次拖动某个维度后，页面按
`0 → 0、T1 → 50、T2 → 100、T3 → 150、T4 → 200` 分段线性重算该维度，并同步更新
详情、雷达图、主导维度和排行；每个维度单独保存自己的百分位设置。没有动过的维度仍
显示导出时的分数。默认导出里 `slide_tricky_score == slide_tricky_raw`，因此调整前直接显示
analyser 结果。

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
