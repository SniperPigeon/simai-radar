# 谱面分析 MVP

核心按显式配置调用独立维度分析器，返回原始指标；当前默认六维依次为 Note、Peak、扫键、
错位压力、星星阵和纵连。评分层按 feature 独立配置映射器：纵连、扫键和 Slide Tricky 暂时
identity 直通，整体物量暂用 2026-09-13 观察批次的中位数与 P99，Peak 使用宽松的探索
锚点，Slide Sequence 使用有限普通谱抽样的取整探索锚点；这些都不代表官方校准。
`slide_cumulate` 的实现与测试仍保留，但默认分析和评分注册已注释停用。

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

## 扫键口径

旧版 `SweepAnalyzer` 按全局拍轴把外键攻击组成时间批次，再用动态规划选择一条最长合法主干。
同一时间批次的其他一至两个物件直接作为辅助物件附在主干上；它们与第二条扫键 Strand
计分等价，因此不再枚举左右手匹配。到达相同末键、方向、速度和连续步数的历史状态只
保留主干更长、变速及折返更少、事件序更早的解释。候选最终按事件覆盖最大、组数最少
选择，不重复计数。

普通 Tap 和不超过十六分音符（`end_beat-start_beat <= 1/4`）的 Hold 进入攻击流；显式
Slide 头已经是 Tap，也会进入。长 Hold 不作攻击，但保留占位区间：若中间键在整个空隙
内被长 Hold 占据，允许同方向跨两个键且用时恰好两个单位间隔。Slide 体、无头 Slide、
Touch 和 TouchHold 不进入。每个普通声明权重为 1，每个 EX 声明权重为 0.3；同拍同键
只形成一个物理速度点，但所有重复声明及其权重都保留。

基础识别门槛是十二分或更快，即每单位键距 `unit_gap_beats <= 1/3`。独立八分扫不识别；
只有已有合法扫键减速至 `1/2 beat` 时，八分段才可进入，并可继续保持八分。单位间隔按
实际键距归一化，因此 Hold 跨键的两键移动使用 `gap/2`。实际秒速度一旦变化立即记录
变速切换；加速、普通减速和减速至八分均增加 0.2。相邻单位秒间隔使用 0.5% 相对容差
（并保留 `1e-9` 秒绝对容差），避免换算抖动伪造反复变速；十二/十六/二十四分等真实
节奏档位差异远大于该容差。

实验开关 `include_paired_sweeps=True` 可识别交替出现的短邻键对：至少四对、组内两键
相邻、组间跨越不相邻键、节奏稳定且每步满足原有十二分门槛。相邻两对须有不同键位，
并要求双手两相扫向稳定且起点逐步移动，或存在周期 2/4 的成对键位重复。由此
`2,3,6,5` 的重复可纳入，普通 `2,3,2,3` 和独立八分交互仍排除。成对短扫的预期
左右手交替不另计 `free_hand_takeover`，但实际空转位移仍由双手 DP 计入。
独立严格消融可指定 `strict_opposite_pairs=True`，额外要求相邻短扫方向相反，且双手 DP
能以“一手完成每对、两手逐对交替、无高速跳跃违规”执行。该条件不能单独识别谱师意图：
Λzure Vixen 的散打配置也满足。再设 `paired_max_interval_seconds=1/12`（180 BPM
十六分）可从本轮正反例中剔除该较慢配置；7 Wonders 的同向两键段会被严格规则排除，
即使其后接双押扫键，因此仍属于需要另行判断的边界例。

实验配置 `eighth_gap_family_bridge=True` 仅允许形状完全相同、速度一致、至少四批且
各有至少三个双押批次的快速扫键组跨越**精确半拍（八分）**空隙形成同一 family；
这不放宽组内八分物件的基础识别门槛。桥接处的双手移动按整段空隙时间计作 idle
reposition，而不是硬判为连续快扫的跳跃违规。两个实验开关默认均关闭；成对短扫的
用手解释属于谱面几何启发式，不能唯一断言玩家实际采用哪只手。

另一个独立消融 `eighth_gap_similar_speed_bridge=True` 只要求两组间隔精确半拍、内部
单位速度差不超过 10%，不要求键位形状相同。它将两组合为新 family，按新首尾时长重新
计算 family 密度；默认三窗 burst 仍按时间点负荷计算，但跨组双手移位会进入该 family。

当前默认 burst 改用更局部的八分接续：若下一组尚无其他前驱，且存在一组恰好在半拍前
结束、单位秒速度差不超过 10%，只给**下一组全部物理键基础负荷 +5%**。该奖励与 EX
声明权重分离，最多计一次，不向后继承，也不合并 family 或将空隙中的双手移动加入 DP。
启用上述实验性 family 桥接时，这项局部奖励不再叠加。

每个时间批次包含一个主干键和最多两个辅助物件；辅助物件参与普通/EX 物件权重，但不
参与速度或方向计算。双押节点可用不同键位进入和离开，例如 `3,4,56,7,8` 以 5 接收
前段、以 6 发出后段；这是同拍换手而非 5 到 6 的瞬时单手移动。同向、异向双扫及单双扫
切换只要存在贯穿的合法最长主干就不会中断；
批次宽度仍保留作审计，但自然扩张、收束及双押交棒都不额外增加权重。双扫批次本身已按
两个物件贡献基础负荷，不再因第二 Strand 或宽度变化重复奖励。
折返只有前一方向已完成至少两步时才成立，最后一段也必须完成两步；转向轴心的 0.2 从
轴心批次开始生效。

速度以 180 BPM 十六分的 `1/12` 秒为单位，使用平方根系数：

```text
speed_factor = sqrt((1/12 second) / unit_interval_seconds)
batch_note_weight = normal_declarations + 0.3 * ex_declarations
if physical_button_count >= 2: batch_note_weight *= 1.3
batch_base = batch_note_weight * speed_factor
```

同一候选内的变速和折返按发生顺序累加倍率，不作音符尾部衰减。不能直接延长
成同一候选、但在上一组一个单位间隔内开始的组仍可组成 family。单押接续若没有同向近
起点或合法折返，只维持 family 关系而不加权；满足方向条件时基础增加 0.2。同拍双押
交棒只维持 family 而不加权。同向且两组起点环形距离不超过一键时再增加 0.1，合法折返仍增加 0.2；
实际变速另增加 0.2。多个前驱可用时取能产生最大线性倍率的前驱，避免乘算次幂爆炸。

```text
multiplier_child = multiplier_parent + connection_increment
family_load = sum(batch_base * current_additive_multiplier)
family_duration = family_end_seconds - family_start_seconds
family_density = family_load / family_duration
eligible = physical_attack_count > 8
family_mean = mean(all_family_density)
duration_factor = sqrt(max(chart_end_time_s, last_event_end_s or 0) / 150 seconds)
mean_load = family_mean * duration_factor
peak = sum(eligible_family_density_(k) / sqrt(k), k=1..min(5, eligible_count))
sweep_raw = 0.6 * mean_load + 0.4 * peak
```

识别只读取 `events-0.3`，伪 EACH 因而沿用 parser 的精确 `1/32 beat`。动态规划状态和
最终候选选择各有 100,000 状态上限，超限时整项失败而不返回部分分数。正时长无扫键为 0；零时长不可用。
每个 family 只用自身首尾覆盖时长归一化，不使用谱面总长度；所有 family 按密度从高到低
排序。物理攻击数不超过 8 的 family 在排名前排除；边界恰好 9 个攻击时
保留，不另设持续时间门槛。Peak 只取合格 family 前五并使用 `1/sqrt(k)` 名次衰减；Mean
则对所有已识别 family（包括物量不超过 8 的短 family）的 density 取算术平均，再乘
`sqrt(chart_duration / 150 seconds)` 作温和总时长补正。最终按
`0.6 * Mean + 0.4 * Peak` 混合。当前公式版本为
`sweep_family_blend_v7_family_mean_duration_sqrt`，识别版本为
`main_spine_v2_chord_handoff`，默认映射暂用 identity。

实验 API `two_hand_motion(times_s, lanes_by_batch)` 使用动态规划最小化双手位移。状态保留
两只手最后位置和最后使用批次；若连续快速批次要求当前手跨越超过可用步数，优先改由
空闲手处理，但空闲手从上次位置到新键位的环形距离仍计入 idle reposition。目标按被迫
快速跳跃次数、自由手接管次数、双手总位移依次最小化。Family 可通过
`sweep_family_hand_motion(family, groups)` 直接计算，返回 active/idle 位移、接管、违规、
每秒和每物件位移及逐批手部分配。Family 起点前的手位未知，因此每只手第一次参与不计
初始放置距离；一旦参与，之后所有空转移位均计入。默认 SweepBurst 以此分配双手动作。

当前默认 `score_sweep_burst` / `SweepBurstAnalyzer` 取全谱三个互不重叠的 2 秒窗口，按
`1/sqrt(k)` 排名衰减后除以权重和，保持每秒强度量纲。窗口基础负荷
只保留普通/EX 物件权重与速度平方根系数，双押倍率固定为 1.0，不继承 family、折返、
变速或接续的累积倍率。全程单押、无变速、无换向的单一 sequence 前 16 个攻击保持
全权，第 `n` 个超额攻击按 `1/sqrt(n+1)` 衰减。双押批次自身保持全权并将连续单押计数
清零，之后从 1 重新累计；换向和变速同样开始新的计数段。同向 family 接续与保持方向的
双押换手只在切换批次局部增加 20%，不向后累乘；结构奖励按物理键数与速度计算，
EX 的 `0.3` 只扣减基础物量，不扣减接续奖励。
空转位移按每只手上次触键到本次触键的完整可用时间计算速度，以 150 BPM 八分音符
移动 2 个键距，即 `10 键/秒` 为参考：
`weighted_idle = distance * sqrt(max(1, (distance / idle_time) / 10))`。
再将 `0.5 * weighted_idle + 1.0 * takeover + 2.0 * fast_jump` 作为等效物量，随后除以
2 秒。Family 内连续简单单押 group 另以 `(hand, direction)` 建立 1 至 4 组的短周期模板；
模板至少覆盖 6 组、重复三轮且匹配率达到 80% 时，符合模板的 group 起点运动负荷只保留
10%，反手或其他不匹配 group 保持全权。双押、换向和变速 group 切断模板；同向 family
接续 group 可参与模板判断但运动负荷受保护、不应用折扣，双押 handoff 同样保持全权。
旧版 `SweepAnalyzer` 仍可由调用方显式注入以作对照。

消融参数可关闭长单押逐项衰减（`simple_run_decay_exponent=0`），改为两类独立模板。
`alternating_idle_multiplier` 启用真正左右交替的两相 group 模板，只缩放匹配组起点的
空转位移项，接管和高速跳跃项保持全权；该参数启用时不再叠加旧的整体运动模板折扣。
可选 `takeover_same_direction_only=True` 进一步限制接管奖励：只在 DP 实际换手且当前组
与父组同向时给 `+1`，反向规律交替没有独立接管奖励，空转位移仍单独计算。
连续至少 16 批、单手、单押、同方向，且单位间隔不慢于 180 BPM 24 分音符的 section
可用 `solo_fast_base_multiplier` 和 `solo_fast_motion_multiplier` 分别缩放基础与运动项。
同类 section 若连续至少 48 批，可用 `solo_fast_long_base_multiplier` 单独覆盖基础倍率，
防止超长循环在取消旧的逐项衰减后重新冲到榜首。
这些参数默认关闭，不改变当前 CLI 算法；首轮消融取交替空转 `0.2`、单手快扫基础 `0.85`、
超长单手快扫基础 `0.4`、运动 `0.5`。

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
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "sweep": IdentityMapper(),
    "slide_tricky": IdentityMapper(),
    "slide_sequence": DummyPnMapper(p50=1.3, p100=2.9),
    "jack": IdentityMapper(),
    # "slide_cumulate": DummyPnMapper(p50=0.36, p100=0.96),
}

class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-sweep-2s-top3-identity-20260916-v48",
        )

TRANSFORMER = DefaultScoreTransformer
```

p50、p100 是 `DummyPnMapper` 的原始指标阈值，要求 `0 < p50 < p100` 且均为有限数值。纵连和扫键
目前用 `IdentityMapper`；纵连不沿用已删除 Hold 频率占位项的 1、2 锚点，扫键等待观察分布；整体物量的
3.540077197、9.328672541 分别来自当前 7512 张观察
样本的中位数和 P99；Peak 的 10、20 是首轮观察用宽松锚点。当前 1888 张难度索引 5/6
观察样本中，`slide_tricky` 曾使用中位数 27.5、P99.9 约 116.06 作为临时锚点；当前默认
改用 `IdentityMapper`，使 `slide_tricky_score` 原样等于 analyser 的 raw 值；
已停用的 `slide_cumulate` 独立复刻版本曾取 0.36、0.96；
`slide_sequence` 暂取 1.3、2.9。它们都是固定临时配置，后续批次不会自动重新拟合。

DummyPnMapper 使用两段线性变换：

```text
x <= 0:           0
0 < x <= p50:     50 * x / p50
p50 < x < p100:   50 + 150 * (x - p50) / (p100 - p50)
x >= p100:        200
```

即 0→0、P50→50、P100→200，范围外截断到 0–200，保留浮点分数、不取整。整体物量在当前临时映射下
3.540077197→50、9.328672541→200；Peak 为 10→50、20→200。NaN、无穷值及无效
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
`provisional-sweep-2s-top3-identity-20260916-v48`；后续调整指标或参数时应同步维护版本。pipeline 校验映射
输出维度与特征配置一致，禁止为失败的原始 feature 生成成功分数。若手动将 TRANSFORMER
设为 None，映射模式仍会明确报错；analysis 不需要评分配置。

pipeline 将评分输出附在 AnalysisRecord.scores 上，导出器追加 `<feature>_score` 并保留映射诊断和版本。映射全部失败时仍保留分数列，以空值表示失败。直接调用导出组件输出原始分析时，可省略评分结果及标准分数列。

### 冻结 mapping profile 并用于开放集

visualizer 的分布页可以导出 `mapping_profile.json`。导出时，对每个默认维度记录当前筛选
范围内 T1–T4 对应的 raw 值、百分位、样本数和最大 raw 值 `t4Max`；profile 顶层记录
`mappingVersion`、固定目标分 `[50, 100, 150, 200]` 与开放集上限 220。所有 raw 锚点
必须严格递增且 T1 大于 0，否则页面会拒绝导出，避免产生有歧义的分段。

```bash
mairadar --mode analysis_score \
  --mapping-profile mapping_profile.json \
  --input data/test-parsed \
  --output outputs/test-scored
```

CLI 校验 profile 后构造 `OpenSetPiecewiseMapper`。设 T3→T4 的斜率
`s = 50 / (T4 - T3)`，则 T4 到 `T4_max`（含端点）统一为 200；超过
`T4_max` 时使用：

```text
score(x) = 200 + 20 * (1 - exp(-s * (x - T4_max) / 20))
```

因此开放集尾部在 `T4_max` 右侧的初始斜率仍为 `s`，随后连续衰减并渐近 220。
新测试集只应用冻结的 raw 锚点，不使用自身分布重新拟合；这也避免测试数据泄漏到校准过程。

## 验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

合成测试覆盖纵连的八分边界、每次飞键的四打断与独立时间上限、重复声明、Top-K 衰减、变速，以及扫键的相邻键、折返、中间攻击、伪 EACH、速度、连续倍率、名次衰减与每秒归一化。另覆盖无效时长、维度失败隔离、批量部分失败、曲绘相对路径、冲突及发布失败。模式测试注入仅用于测试的映射器，验证 full 与 analysis_score 等价、analysis 不映射不导出、full 不读写中间 bundle，以及映射失败继续处理。另用自定义分析器和内存导出替身验证各层可替换。dummy Pn 另有锚点、区间插值、截断、独立参数和错误隔离测试，identity 另有直通与非法值测试，并通过真实 CLI 对合成输入运行 full / analysis_score 验证完整输出；不代表已完成官方校准。文件夹选择的选择、取消、不可用分支通过 mock 验证，不代表已进行原生窗口人工验收。
