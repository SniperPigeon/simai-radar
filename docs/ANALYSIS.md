# 谱面分析 MVP

核心按显式配置调用独立维度分析器，返回原始指标；当前默认配置包含纵连、整体物量、
Peak 爆发和 Slide 压力。评分层按 feature 独立配置映射器：纵连和 Slide Tricky 暂时
identity 直通，整体物量暂用 2026-09-13 观察批次的中位数与 P99，Peak 使用宽松的探索
锚点，其他 Slide 维度使用有限普通谱抽样的取整探索锚点；这些都不代表官方校准。

## 直接调用

```python
from mairadar.parser import parse_chart
from mairadar.analysis import ChartAnalyzer

parsed = parse_chart("(180){16}1h[4:1],1,E")
result = ChartAnalyzer().analyze(parsed)
assert result.features["jack"].data == 2.6
```

调用不需要歌曲 metadata、路径、音频或曲绘，不加载文件读写、评分模块或 GUI。输入沿用 ParseResult 的完整性和时间字段，保留 parser 的原文诊断。不会重新解析 Simai。

## 配置与扩展

默认配置位于 `src/mairadar/analysis/config.py`，也可以由调用方传入：

```python
from mairadar.analysis import ChartAnalyzer, FeatureResult
from mairadar.analysis.features import JackSequenceAnalyzer

features = {"jack": JackSequenceAnalyzer}
analyzer = ChartAnalyzer(features=features)
```

每个类实现 `analyze(context) -> FeatureResult`，且支持无参数构造。名字为字母开头的字母、数字、下划线组合。结果和 CSV 列按配置顺序输出；不限制为六维。类在每张谱面每个维度调用时重新构造，避免跨谱面残留状态。

AnalysisContext 提供事件元组、chart_end_time_s、last_event_end_s 和 duration_s。各维度应只读事件；调度器为每个维度复制输入快照，防止意外修改影响调用方或其他维度。MVP 不预先建立窗口索引或几何缓存。

FeatureResult 仅包含 data 和 success 两个字段，不携带单位、中间统计量或诊断。成功时 data 为有限数值；失败时 data=None、success=False。维度抛出的异常由主分析器记录到 AnalysisResult.diagnostics，其他维度仍执行；汇总状态为 ok、partial 或 error。

## 纵连口径

```text
sequence_strength = 主键 Tap/Hold 数 + 1.5 * 有效打断 Tap 数
equivalent_sixteenth_bpm = 15 * (主键时间点数 - 1) / (末次主键秒数 - 首次主键秒数)
speed_factor = (equivalent_sixteenth_bpm / 180) ^ 1.5
weighted_strength = sequence_strength * speed_factor
rank_weight_(x) = 1.3 * 0.645 ^ (x - 1), x=1..5
jack_raw = sum(weighted_strength_(x) * rank_weight_(x),
               x=1..min(5, sequence_count))
```

`JackSequenceAnalyzer` 只读取 1–8 外键的 Tap/Hold 声明时间，不使用 Hold 持续尾部，
也不把 Touch、TouchHold 或 Slide 体放入外键时间流。按全局有理拍轴为每个键位建立
最大连续候选：

- 主键必须在至少两个不同时间点出现；Tap 与 Hold 都按一个主键物件计。同时间同位置的
  重复声明仍各自计数，但只有一个时间点的重复声明不单独构成纵连。
- 时间流中任意相邻外键时间点的间隔必须 `<= 1/2 beat`；超过即结束当前候选。
- 至少连续形成两个主键时间点后才允许飞键。一次飞键可占用多个连续异键时间点，合计
  最多四个其他位置的普通 Tap；数量上限对每次飞键独立应用，不跨序列累计。
- 每次飞键的时间为从首个异键时间点到返回主键的拍长，必须 `<= 1/2 beat`。回到主键
  后，数量和时间预算均重置，但需从该次返回开始重新累计两个主键时间点才能再次飞出。
  边界值恰好八分音符时保留。
- 异键 Hold 不能作为打断，会结束当前键位候选。与主键同拍的其他位置物件不位于两个
  主键时间点之间，不计打断权重。

速度使用主键不同时间点的实际秒数计算，因此自然包含 BPM 变化；同时重复声明增加主键
物件权重，但不增加速度采样点。180 BPM 等效十六分的系数为 1，快慢两侧均按 1.5 次方
变化，使速度影响高于线性；180 BPM 八分仍是合法连续候选，但速度系数仅约 0.354。

每个键位按上述规则切成若干不可继续延伸的最大候选；候选按 `weighted_strength` 从高到
低排序，取前五条后使用 `1.3 * 0.645^(x-1)` 的几何名次权重。五条等强候选时 Top‑1
约占全部名次权重的 40%，随后各名约为前一名的 64.5%。不足五条不补零也不做均值
归一化。正常完整谱面没有纵连时为 0；零时长谱面返回
`FeatureResult(None, success=False)`。

解析不完整或模型校验失败时，不执行子分析器和映射器，各维结果均标记失败，主分析器
分别记录 PARSE_INCOMPLETE 或 INVALID_INPUT。该指标直接使用 parser 提供的全局拍轴，
不重新扫描原始 Simai，也不受 BPM 变速或重复同值 BPM 声明影响。

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

Tricky 先从全谱所有有效 Slide 路径收集声明到启动的正等待时长，按最近的 0.05 秒
整数倍分桶（半桶边界向上）；选择频数最高的桶，并列时选择较长的一桶，再以桶内
实际值的中位数作为众数等待基准。零等待不参与基准，但仍保留；没有正等待时不启用过滤。
等待严格超过基准四倍的路径不再作为 Tricky 的计分目标，恰好四倍保留。同头多路径
分别参与统计和过滤，连接路径不按连接段重复计样本。

此过滤只影响 Tricky，不删除原事件，不改变 `slide_sequence` 或 `slide_cumulate`。
先完成原有干扰归属，再移除异常目标路径；被移除配置的物件不回流到其他配置。
混合配置仅保留正常路径，并在原先分配给它的物件内重新检查等待、启动与活动窗口，
重算负荷和多头/多路径提升。

三个 Slide 分析器只读取 events-0.3，不重新扫描 Simai。共享同一 `head_event_id` 的路径先
组成一头多路径组；声明秒时间差不超过 `1/60` 秒的多头组再合成同一启动配置。零持续
时间路径跳过。一个外部物件始终只归属一个配置：优先归入恰好相同的启动拍，否则归入
离启动最近的待启动配置，最后才归入最近启动且仍在运动期内的配置。同刻多头已经合为
同一配置，因此也不会在配置内部重复。

对配置 `j`，在声明到启动之间计算内部干扰 `I_j`。Tap/Hold 基础权重为 1；与本配置任一
外键头同位时使用 1.5 倍；若该 Tap 确实关联至少一条有效 Slide 路径，则使用 2 倍。多个
条件取最大值而不乘算，因此同位的实际 Slide 头仍为 2，而不是 3。只有
`force_star` 等显示效果而不带路径的 Tap 不使用 2 倍。等拍距、每拍向相邻键同方向移动的单扫
或双扫，以时间点数 `n` 衰减当前批次：

```text
sweep_factor(n) = 1 / log2(max(2, n - 1))
```

所以前三个时间点不减，第四个起衰减；双扫的批次基础权重自然为单扫两倍。方向、拍距或
单/双扫宽度变化会重新起算。Touch/TouchHold 仍先按同拍空间连通组成组，每组权重 1.5，
但每个启动配置按时间顺序最多计两组。期间启动的其他 Slide 体每条路径权重为 1。

普通 Tap/Hold 另使用有界速度系数。同拍双押只算一个时间点；把 Slide 声明时间到第一个
普通按钮、以及之后相邻普通按钮之间的正秒间隔取中位数 `dt_med_j`，等效八分 BPM 与
速度系数为：

```text
B_eq_j = 30 / dt_med_j
ratio_j = B_eq_j / 180
speed_j = sqrt(ratio_j)                 if ratio_j < 1
speed_j = min(1.5, ratio_j)             if ratio_j >= 1
```

180 BPM 八分为 1；更慢时按平方根温和下降，更快时线性提升并在 1.5 封顶。该系数只乘
等待期和一拍运动期的普通 Tap/Hold 贡献；实际 Slide 头、Touch、Slide 体和启动拍均保持
原权重。没有可用正间隔时系数为 1。

恰在本配置任一启动时刻的物件改计入启动负荷 `L_j`，不再套同位或待启动头乘数：

```text
L_j = sum(tap_weight + attached_slide_path_count) / 2
```

普通 Tap 因而为 0.5，Touch 组为 0.75，新按下的一头一路为 `(1+1)/2=1`，一头两路为
`(1+2)/2=1.5`。本配置自身的头和路径始终排除。令
`U_j = sum_h sqrt(path_count_h)`，对合并后的多头/多路径配置只作小幅后置提升：

启动后还会计算一段受限的运动期。对每条路径 `p`，范围为
`(launch_p, min(end_p, launch_p + 1 beat)]`；一拍通过全谱 beat/BPM 映射换算，不是固定
秒数。同一配置的多路径区间取并集，物件仍只计一次。运动期 Tap 不再使用原头同位 1.5，
但实际 Slide 头 2 倍、扫键衰减、Touch 权重及最多两组的上限继续适用。启动后一拍以外的
交互不再归因于该 Slide，避免把玩家已经可以撒手处理的长 Slide 全程计入。

令 `N_j` 为该配置实际计入的逻辑干扰物件数，范围包括等待期、启动拍和一拍运动期：
Tap/Hold 与 Touch 组各算 1，新 Slide 头 Tap 和它带出的每条路径分别计数，独立启动的每条
Slide 路径也各算 1。Touch 的两组上限先应用。超过 16 时不任意删除具体物件，而按平均
贡献把整个配置缩放到 16 个有效物件：

```text
cap_j = min(1, 16 / N_j)                                  if N_j > 0
cap_j = 1                                                  if N_j = 0
Q_j = (I_j + L_j) * cap_j * (1 + 0.15 * max(0, U_j - 1))
```

单头单路径不变，双头双路径提升 15%，单头双路径提升约 6.2%。将所有 `Q_j` 从高到低
排列为 `Q_(1), Q_(2), ...`，保留重复负荷，不足五个时补 0；`slide_tricky`
取前五强按对数衰减权重的归一化加权平均：

```text
w_k = 1 / log2(k + 1)                                  k = 1…5
slide_tricky = sum(w_k * Q_(k)) / sum(w_k)
```

分母始终包含五个位置的权重；Top‑1 占约 33.916%。`tricky_total_load` 仍保留前五项
未加权之和，便于查看配置负荷；不再等于 `slide_tricky * 5`。

`slide_cumulate` 使用独立 analyser，不复用上述当前 Tricky helper。它重新建立 Slide 组、
onset 与等待窗归属：外部物件全谱只归给最近启动的一个 onset；启动前内部物件使用
`F(n)=n+0.35*log2(n!)`，启动同拍线性，Touch 组为 1.5，不含同位、扫键、活动期或多头
uplift。连续段强度为 `0.8*mean + 0.2*RMS`，有效长度前 5 个线性，之后使用底数 5：

```text
D = max(chart_end_time_s, last_event_end_s or 0)
m_eff = m                                                   if m <= 5
m_eff = 5 + 5 * log5(1 + (m - 5) / 5)                     if m > 5
I_r = 0.8 * mean(Q_c) + 0.2 * RMS(Q_c)
L_r = m_eff * I_r
slide_cumulate = sum(L_r) / (D / 0.5)
```

`slide_sequence` 仍独立按声明拍分连续段：相邻启动配置满足 `0 < delta_beat <= 1` 即连续，
夹杂其他物件不打断。它使用实际秒间隔的 `mean(0.5/delta_time)`、从第四个时间点后增长
放缓的长度因子，以及 `0.5 * mean(max(0,U_j-1))` 并发加项；全局取非零段强度的 RMS。
正时长无 Slide 谱面三个维度均为 0，零时长返回失败。

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
# 以下模式默认使用按 feature 配置的映射器：
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
from mairadar.scoring import DummyPnMapper, IdentityMapper


FEATURE_MAPPERS = {
    "jack": IdentityMapper(),
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "slide_tricky": IdentityMapper(),
    "slide_cumulate": DummyPnMapper(p50=0.36, p100=0.96),
    "slide_sequence": DummyPnMapper(p50=1.3, p100=2.9),
}

class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-jack-tricky-identity-20260915-v29",
        )

TRANSFORMER = DefaultScoreTransformer
```

p50、p100 是 `DummyPnMapper` 的原始指标阈值，要求 `0 < p50 < p100` 且均为有限数值。纵连
目前用 `IdentityMapper`，不沿用已删除 Hold 频率占位项的 1、2 锚点；整体物量的
3.540077197、9.328672541 分别来自当前 7512 张观察
样本的中位数和 P99；Peak 的 10、20 是首轮观察用宽松锚点。当前 1888 张难度索引 5/6
观察样本中，`slide_tricky` 曾使用中位数 27.5、P99.9 约 116.06 作为临时锚点；当前默认
改用 `IdentityMapper`，使 `slide_tricky_score` 原样等于 analyser 的 raw 值；
`slide_cumulate` 独立复刻版本中位数约 0.363、P99 约 0.960，临时取 0.36、0.96；
`slide_sequence` 暂取 1.3、2.9。它们都是固定临时配置，后续批次不会自动重新拟合。

DummyPnMapper 使用两段线性变换：

```text
x <= 0:           0
0 < x <= p50:     50 * x / p50
p50 < x < p100:   50 + 150 * (x - p50) / (p100 - p50)
x >= p100:        200
```

即 0→0、P50→50、P100→200，范围外截断到 0–200，保留浮点分数、不取整。整体物量在当前临时映射下
3.540077197→50、9.328672541→200；Peak 为 10→50、20→200；`slide_cumulate` 为
0.36→50、0.96→200。NaN、无穷值及无效
阈值明确报错。

同一映射器类可以配置不同阈值，也可以替换为其他实现 map 的类。调用方可以直接注入自己的配置：

```python
from mairadar.pipeline import run_pipeline
from mairadar.scoring import DummyPnMapper, FeatureScoreTransformer

mapper = FeatureScoreTransformer(
    {"jack": DummyPnMapper(p50=2.0, p100=6.0)},
    mapping_version="my-jack-v1",
)
batch, report = run_pipeline(
    "analysis_score", "data/parsed", output="outputs/scored", transformer=mapper,
)
exit_code = max(batch.exit_code, report.exit_code)
```

原始 feature 失败时不调用其映射器，标准分数留空。缺少某个 feature 的配置，或它的映射器报错、返回非有限值时，只将该 feature 标为失败，其他 feature 继续映射，原始数据保留；批次返回非零。多余配置允许存在，便于分析器选择特征子集。

ScoreResult 独立存储标准分数与 mapping_version。默认版本为
`provisional-jack-tricky-identity-20260915-v29`；后续调整指标或参数时应同步维护版本。pipeline 校验映射
输出维度与特征配置一致，禁止为失败的原始 feature 生成成功分数。若手动将 TRANSFORMER
设为 None，映射模式仍会明确报错；analysis 不需要评分配置。

pipeline 将评分输出附在 AnalysisRecord.scores 上，导出器追加 `<feature>_score` 并保留映射诊断和版本。映射全部失败时仍保留分数列，以空值表示失败。直接调用导出组件输出原始分析时，可省略评分结果及标准分数列。

## 验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

合成测试覆盖纵连的八分边界、每次飞键的四打断与独立时间上限、重复声明、Top-K 衰减、变速、无效时长、维度失败隔离、批量部分失败、曲绘相对路径、冲突及发布失败。模式测试注入仅用于测试的映射器，验证 full 与 analysis_score 等价、analysis 不映射不导出、full 不读写中间 bundle，以及映射失败继续处理。另用自定义分析器和内存导出替身验证各层可替换。dummy Pn 另有锚点、区间插值、截断、独立参数和错误隔离测试，identity 另有直通与非法值测试，并通过真实 CLI 对合成输入运行 full / analysis_score 验证完整输出；不代表已完成官方校准。文件夹选择的选择、取消、不可用分支通过 mock 验证，不代表已进行原生窗口人工验收。
