# 谱面分析 MVP

核心按显式配置调用独立维度分析器，返回原始指标；当前默认配置包含用于验证流程的 HOLD
频率、整体物量、Peak 爆发和 Slide 压力。评分层按 feature 独立配置映射器：HOLD 仍使用
dummy 锚点，整体物量暂用 2026-09-13 观察批次的中位数与 P99，Peak 使用宽松的探索
锚点，Slide 使用有限普通谱抽样的取整探索锚点；这些都不代表官方校准。

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

正常完整且时长为正的无 Hold 谱面得到 FeatureResult(0, success=True)；零时长返回 FeatureResult(None, success=False)。解析不完整或模型校验失败时，不执行子分析器和映射器，各维结果均标记失败，主分析器分别记录 PARSE_INCOMPLETE 或 INVALID_INPUT。连接 Slide 的各段时间由 parser 按固定 MajdataPlay bar 数分配，分析器可直接读取，不重新扫描原始 Simai。

## 整体物量口径

`NoteDensityAnalyzer` 使用从谱面时间原点开始、互不重叠的 1.5 秒窗口。末窗按完整
1.5 秒补零；事件恰好位于谱面结束点时归入末窗。分析时长仍为
`max(chart_end_time_s, last_event_end_s)`，不叠加音频 offset。

每个窗口先累加以下补正物量：

- Tap 为 1；Slide 的显式星星头仍是独立 Tap；
- 普通 Hold 的全局拍轴长度不超过八分音符（`end_beat-start_beat <= 1/2`）为 1，
  否则为 2；TouchHold 进入 Touch 规则；
- 同一 Slide 组先汇总全部连接段与共享头/无头分支的 `bar_count`，再计算
  `ceil(total_bar_count / 64)`；组归入最早的实际 Slide 开始时间；
- Touch 先在同一时间按传感器邻接求连通分量。不同时间的分量若相距不超过
  十六分音符（全局拍轴 `<= 1/4`）且空间相邻，则按时间顺序优先合并；已经跨时间
  合并的分量不再参与下一次合并，因此一个最终组最多包含两个时间点。每组为 1。

Touch 邻接只认图中共享边，循环下标按 1–8 取模：

```text
C   : B1..B8
A_i : B_i, D_i, D_(i+1), E_i, E_(i+1)
B_i : A_i, B_(i-1), B_(i+1), C, E_i, E_(i+1)
D_i : A_(i-1), A_i
E_i : A_(i-1), A_i, B_(i-1), B_i
```

C1/C2 在 parser 中统一为逻辑 `C`；只在角上接触的区域（例如 D1/E1）不邻接。
同时同位置的重复声明不会仅因位置相等而去重。

设窗口补正物量为 `w_i`，则：

```text
x_i = w_i / 1.5
mu = population_mean(x_i)
CV = population_std(x_i) / mu
note_density_raw = mu * (1 + 0.3 * CV)
                 = mu + 0.3 * population_std(x_i)
```

正时长空谱得到 0；零时长不可用。`start_beat/end_beat` 是精确有理数字符串，足够完成
八分音符和十六分音符判断；当前 schema 不导出小节边界或 `{分拍}` 时间线，`||s`
拍号扩展也尚未支持。本口径不依赖小节边界。

单独启用整体物量的方式：

```python
from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import NoteDensityAnalyzer

analyzer = ChartAnalyzer({"note_density": NoteDensityAnalyzer})
```

## Peak 爆发口径

`PeakDensityAnalyzer` 复用整体物量的 Hold 长短判定、Slide 归组和 Touch 邻接归组，但使用
独立的爆发权重：每个 Slide 组固定为 1，不按几何长度增加；每个 Touch/TouchHold 组为
0.5。候选中心窗宽 1.5 秒，起点从谱面时间 0 开始每 0.5 秒移动一次；每个候选同时读取
其左侧、中心和右侧三个互不重叠的 1.5 秒窗口：

```text
L(s) = density([s-1.5, s))
C(s) = density([s, s+1.5))
R(s) = density([s+1.5, s+3.0))

q(s) = 0.2 * L(s) + 0.6 * C(s) + 0.2 * R(s)
```

谱面边界外按零物量处理。候选按 `q(s)` 从高到低选择；每个候选覆盖
`[s-1.5, s+3.0)`，与已经入选的 4.5 秒区间重叠时跳过，最多取得三个互不重叠的峰
`p1 >= p2 >= p3`。不足三个时以零补齐：

```text
peak_raw = 0.5 * p1 + 0.3 * p2 + 0.2 * p3
```

因此一次孤立尖峰只能取得其局部峰值的 50%，反复出现的爆发才会补足其余权重。
0.5 秒步长降低了爆发落在固定 1.5 秒边界两侧时的切分敏感性。实现使用排序后的补正
物量点和前缀和查询窗口，不复制跨窗口事件。

Peak 还识别外键 Tap/Hold（包括 Slide 星星头）组成的扫键。按全局有理拍轴排序后，只有
非同时、相邻键位、方向相同且拍间隔完全一致的连续序列才会延长；方向变化、非相邻键或
同拍多键会断开当前序列。设物件是当前扫键的第 `n` 个，则：

```text
adjusted_weight(n) = base_weight / log2(max(2, n - 2))
```

前四个权重不变；第五个除以 `log2(3)`，第六个除以 2，之后继续对数衰减。这里使用
`max(2, n-2)`，因为写成 `min` 会让第五个以后的除数恒为 1，无法产生预期衰减。

## Slide 压力口径

`SlidePressureAnalyzer` 只读取 events-0.3 事件，不重新扫描 Simai。实现、导出列和界面统一
使用 `slide`，不使用口语化的星星命名。共享同一 `head_event_id` 的路径为一个 Slide 组；
无头分支按相同来源声明分组。

对组 `i` 中每条路径 `p`，令 `L_p` 为全部段的 `bar_count` 之和：

```text
M_p = sqrt(L_p / 20)
M_i = sum(M_p for p in group_i)
```

20 bar 是首版固定参考。自体压力不读取速度，避免 `v=L/T` 再乘长度平方根造成长度实际按
`L^1.5` 重复贡献。零持续时间路径仍按输入异常直接跳过；同组其他有效分支继续计算，整组
均无有效路径时跳过整组。不会为这种路径生成任意截断高分，也不会仅因此令整张谱的
Slide 维失败。

每个 Slide 组的启动干扰使用包含两端的 `[declare_time, latest_launch_time]`。共享头的分支
可能分别启动，因此窗口保持到最后一个分支启动。排除本组自己的头与启动动作；普通
Tap/Hold onset 和其他 Slide 启动动作权重为 1，同拍相邻 Touch/TouchHold 连通组权重为
1.5。Touch 不跨时间合并，避免把恰在启动点的动作移动到窗口之外。启动时刻的其他物件
明确计入。同一物件若同时干扰多个待启动 Slide，可分别进入各自的 `Q_i`：

```text
Q_i = sum(weight(e) for declare_i <= time(e) <= launch_i, excluding group_i)
P_i = M_i + 0.5 * Q_i
```

Slide 按声明拍排序，声明秒时间差不超过 `1/60` 秒的 Slide 先形成同一 onset cluster，
cluster 内不产生 cadence 边。这会把 `{9999}` 等极细分拍编码的准同时 Slide 视为同一
次人体动作，避免 `0.5 / delta_time` 在零附近发散。相邻 cluster 只要满足
`0 < delta_beat <= 1` 就属于同一个最大连续段，因此四分、附点八分、八分、三连音和更快
间隔都连续；夹杂的非 Slide 物件不打断。声明拍有头时取关联头的 `start_beat`；无头时由
`slide_declare_time_s` 和 timing/BPM 事件回算，不重新读取原文。

一段 `r` 有 `n_r` 个 Slide 组、`m_r` 个 onset cluster。实际秒间隔而非 BPM 标签确定速度：

```text
A_r = 1                                                   if m_r == 1
A_r = mean(0.5 / delta_time_k for adjacent clusters k)   if m_r >= 2
```

`G_r` 是第一至最后声明之间、不属于本段自身且未进入任何启动窗口的剩余夹杂物量。它只
补充窗口外的段内上下文；已经进入一个或多个 `Q_i` 的物件不再进入 `G_r`：

```text
C_r = (sum(Q_i for i in r) + G_r) / n_r
S_r = mean(M_i for i in r) * A_r + 0.5 * C_r
```

`C_r` 作为加权加项，不再被 cadence 乘算。连续段前 8 个 Slide 完整计数，之后有效长度
按自然对数增长：

```text
n_eff_r = n_r                                            if n_r <= 8
n_eff_r = 8 + 8 * ln(1 + (n_r - 8) / 8)                 if n_r > 8
R_r = sqrt(n_eff_r) * S_r
```

`S_r` 是单位 Slide 的段强度，`R_r` 是使用 log 软上限后的段压力。全局不取最大段，而把
每段的平方压力全部纳入：

```text
D = max(chart_end_time_s, last_event_end_s or 0)
I_slide = sqrt(sum(n_eff_r * S_r**2) / sum(n_eff_r))
rho_slide_eff = 60 * sum(n_eff_r) / D
rho_slide_actual = 60 * sum(n_r) / D
slide_raw = I_slide * sqrt(rho_slide_eff)
          = sqrt((60 / D) * sum(R_r**2))
```

正时长无 Slide 谱面得到 0，零时长不可用。`slide_pressure_breakdown` 可供测试和实验代码
检查每段的 `M/C/A/S/R`、实际/有效 Slide 数、全局活跃强度与实际/有效每分钟 Slide
密度；默认 `FeatureResult` 仍只输出 `slide_raw`，不扩展通用结果契约。

## 统一 CLI 与模式

统一入口为 `scripts/mairadar.py`，安装包后使用 `mairadar`，或设置 PYTHONPATH=src 后使用
`python -m mairadar`。四种模式由外围 pipeline 组合：

| --mode | 输入 | 执行步骤 | 输出 |
| --- | --- | --- | --- |
| full | 原始 Simai 文件或目录 | 解析 → 特征分析 → 映射 → 导出 | 总表 CSV 与曲绘 |
| parse_only | 原始 Simai 文件或目录 | 解析 → bundle 写入 | 每张谱面一个事件 bundle |
| analysis | 已导出的 bundle 根目录 | 特征分析 | 终端逐行 JSON，不写文件 |
| analysis_score | 已导出的 bundle 根目录 | 特征分析 → 映射 → 导出 | 总表 CSV 与曲绘 |

```bash
python scripts/mairadar.py --mode analysis --input data/parsed
python scripts/mairadar.py --mode analysis --choose
# 以下模式默认使用 dummy Pn 映射器：
python scripts/mairadar.py --mode analysis_score --input data/parsed --output outputs/scored
python scripts/mairadar.py --mode full --input data/raw --output outputs/full --difficulty 5 6
python scripts/mairadar.py --mode parse_only --input data/raw --output data/parsed --difficulty 5 6
```

需要 Python 3.11+。`--choose` 延迟加载 tkinter 打开输入目录选择器；没有 tkinter 或 GUI
时使用 `--input`。取消选择返回非零。`--difficulty` 可用于所有模式，`--chart-type` 仅供
full 与 parse_only 使用。analysis 不接受 --output，其他模式必须提供 --output；`--format`
只作用于会产生评分报告的 full 与 analysis_score。

full 对目录递归发现 maidata.txt、majdata.txt 和 .simai；解析一次后直接把内存事件交给
分析器，不导出或重新读取中间 bundle。执行分析的 full、analysis 和 analysis_score 默认
只选择 `difficulty_index=5` 的 Master 与 `difficulty_index=6` 的 Re:Master；使用
`--include-utage` 会在默认集合上追加 7 号宴谱，显式 `--difficulty` 则完全覆盖默认选择。
类型优先使用 --chart-type、显式 metadata，缺失时由解析后的独立 DX 检测器补全。

批处理跳过空正文、纯注释及只有时间指令/休止而没有物件的谱面，parse_only 也不导出这些空包。含非法物件或其他实质错误的谱面仍返回失败；显式请求不存在的难度仍报告 MISSING_CHART。纯文本 parser 保留原有空谱诊断，跳过逻辑位于外围批处理层。

parse_only 使用同样的原始文件发现、难度选择和类型优先级，但解析后直接调用 bundle
写入层。每张成功写入的谱面保留 events、metadata、解析诊断、完整性状态和可选曲绘；
它不导入 analysis 或 scoring 实现。不完整谱面和单文件失败不会阻断其他谱面，但批量结果
返回非零。纯解析和事件 bundle 导出也继续支持 parse_file / write_bundle 库 API。

analysis 和 analysis_score 将根目录的每个直接子文件夹作为现有 CSV bundle 读取，不递归，
也不自动发现 maidata；同样默认只选择 5、6，并接受 `--include-utage` 或显式
`--difficulty`。直接子文件忽略。损坏、缺少文件或不完整的 bundle 均保留结果行；其他
目录继续处理。空输入、批量部分失败、映射失败和导出失败均返回非零。

analysis 的终端 JSON 每张谱面一行，包含 metadata、`analysis.features` 下各维的 data/success 和诊断；既可直接检查，也可由外部调用者消费。摘要和错误提示不混入 JSON 行。

## 解析后的 DX 检测

`mairadar.chart_type.detect_chart_type(ParseResult)` 只读取已解析事件，不重新扫描文本。类型优先级为调用方 --chart-type、显式 metadata、检测结果；语法 parser 和 parse_file 不负责推断类型。

按当前约定，命中以下任一项即为 DX：Touch、TouchHold、组合星星（同一 Slide 路径包含多个连接段）、任意 EX（包含 EX BREAK）、BREAK Hold、BREAK Slide 路径、BREAK 星星头或强制星形 BREAK。普通 BREAK Tap、普通 Hold、普通单段 Slide 不触发 DX；@ 标记的 Tap 头按 Tap 处理。

完整谱面未命中时按 SD 补全。这是基于当前物件规则的缺省分类，不代表识别官方发行版本；有显式类型时不会覆盖。incomplete 返回 None，不做分类，也不进入后续特征计算和映射。检测结果只写入外围 chart metadata，不修改事件或源文件。

三个会进入分析层的 CLI 模式共用 bundle 到分析结果的适配步骤，因此都可以补全缺失
类型；parse_only 在写 bundle 前调用同一个独立检测器。新增检测不进入语法 parser，也不
增加外部依赖。直接调用示例：

```python
from mairadar.chart_type import detect_chart_type
from mairadar.parser import parse_chart

chart_type = detect_chart_type(parse_chart("(120)A1,E"))  # "dx"
```

## 总表导出

输出目录必须是新目录或空目录，且 pipeline 不允许输入输出互相包含。导出先写临时目录再发布，不覆盖已有报告或用户文件。

```text
outputs/analysis/
  charts.csv
  covers/
    曲名-5-dx.png
```

CSV 使用 UTF-8 BOM，固定列为 title、artist、designer、difficulty_index、level、chart_type、cover_path、status、diagnostics，后面追加配置中的 `<feature>_raw`。失败值留空，diagnostics 为 JSON，包含源子目录、解析和主分析器诊断、各维成功与否。普通 metadata 缺失时留空；DX/SD 缺失时按下述独立检测规则补全。

曲绘按原样复制，cover_path 相对 CSV 所在目录。导出期按来源、标题和曲师临时归组，同曲全部难度及 DX/SD 谱面共享一份按标题命名的曲绘；不创建歌曲注册表或稳定身份。同名文件字节完全相同时复用；不同歌曲同名但内容冲突，或同一歌曲内出现不同曲绘时，保留导出诊断且不覆盖已有文件，不改变分析/评分状态或增加失败计数。CSV 与 visualizer 使用相同策略，不追加 hash，也不去重报告行。

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
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "slide": DummyPnMapper(p50=4.6, p100=27.0),
}

class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-slide-20260913-v8",
        )

TRANSFORMER = DefaultScoreTransformer
```

p50、p100 是原始指标的数值阈值，要求 `0 < p50 < p100` 且均为有限数值。默认 HOLD 的
1、2 仅是 dummy 参数；整体物量的 3.540077197、9.328672541 分别来自当前 7512 张观察
样本的中位数和 P99；Peak 的 10、20 是首轮观察用宽松锚点；Slide 的 4.6、27 来自当前
7326 张非宴谱的约 4.64 中位数和 26.59 P99 后取整，只用于首轮实现检查。它们都是
固定的临时配置，后续批次不会自动重新拟合。

DummyPnMapper 使用两段线性变换：

```text
x <= 0:           0
0 < x <= p50:     50 * x / p50
p50 < x < p100:   50 + 150 * (x - p50) / (p100 - p50)
x >= p100:        200
```

即 0→0、P50→50、P100→200，范围外截断到 0–200，保留浮点分数、不取整。默认 HOLD
示例：0.5→25、1→50、1.5→125、2→200；整体物量在当前临时映射下
3.540077197→50、9.328672541→200；Peak 为 10→50、20→200；Slide 为
4.6→50、27→200。NaN、无穷值及无效阈值明确报错。

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

ScoreResult 独立存储标准分数与 mapping_version。默认版本为
`provisional-slide-20260913-v8`；后续调整指标或参数时应同步维护版本。pipeline 校验映射
输出维度与特征配置一致，禁止为失败的原始 feature 生成成功分数。若手动将 TRANSFORMER
设为 None，映射模式仍会明确报错；analysis 不需要评分配置。

pipeline 将评分输出附在 AnalysisRecord.scores 上，导出器追加 `<feature>_score` 并保留映射诊断和版本。映射全部失败时仍保留分数列，以空值表示失败。直接调用导出组件输出原始分析时，可省略评分结果及标准分数列。

## 验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

合成测试覆盖已知频率、重复声明、持续物件尾部、变速、无效时长、维度失败隔离、批量部分失败、曲绘相对路径、冲突及发布失败。模式测试注入仅用于测试的映射器，验证 full 与 analysis_score 等价、analysis 不映射不导出、full 不读写中间 bundle，以及映射失败继续处理。另用自定义分析器和内存导出替身验证各层可替换。默认 dummy Pn 另有锚点、区间插值、截断、独立参数和错误隔离测试，并通过真实 CLI 对合成输入运行 full / analysis_score 验证完整输出；不代表已完成官方校准。文件夹选择的选择、取消、不可用分支通过 mock 验证，不代表已进行原生窗口人工验收。
