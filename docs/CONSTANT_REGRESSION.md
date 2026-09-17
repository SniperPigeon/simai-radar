# 日本谱面定数与回归 CLI

定数采集、训练、推理独立于 visualizer，也不进入 parser、analysis 或 scoring。
统一使用日本定数：两份日本数据自动一起读取，不配置 region，不按删除状态筛选、分组或展示。
匹配到明确的定数就纳入训练；没有定数或只有网页估值的谱面保留记录但不训练。

需要 Python 3.11+。训练使用 scikit-learn（及其数值计算依赖），采集和推理只依赖标准库：

```bash
python -m pip install -e '.[regression]'
```

本机可直接使用 `/opt/anaconda3/bin/python`；系统自带的 `python3` 是 3.9，不满足项目要求。
下列命令在仓库根目录运行。输出必须使用新路径，避免覆盖已有实验。
示例中的 `outputs/official-analysis` 和 `outputs/fanmade-analysis` 分别是用当前版本生成的官谱、
自制谱 visualizer；从事件 bundle 生成的例子见下方导出命令。

## 从采集到导出

```bash
# 1. 一次抓取全部日本定数来源
python scripts/constants.py fetch --output data/constants/otoge-japan.json

# 2. 为已有 raw 结果生成总表，不重新分析谱面
python scripts/constants.py match \
  --input outputs/official-analysis \
  --snapshot data/constants/otoge-japan.json \
  --output outputs/constants.csv

# 3. 固定四次多项式并自动导出模型
python scripts/constant_regression.py fit \
  --input outputs/constants.csv \
  --output outputs/constant-fit-quartic \
  --degrees 4
```

`match` 存在未匹配行时会写出完整总表并返回 1，所以这几步分开运行。
确认匹配统计后，`fit` 会自动筛选可训练行。`fit` 的输出目录中已经包含：

| 文件 | 用途 |
| --- | --- |
| `model.json` | 全部有效标签重拟合后的推理参数，可直接导出给调用方 |
| `evaluation_model.json` | 只在 development 集拟合，用于复现留出集误差 |
| `report.json` | 候选参数、分组、MAE/RMSE/R²、基线和排除统计 |
| `predictions.csv` | 所有输入行的拟合定数、状态、排除原因和留出预测 |
| `test_vectors.json` | 模型输入列及预期双精度输出，供其他推理实现对照 |

**没有额外的 export 步骤。** 训练完成后复制 `model.json` 即可；模型推理不需要 CSV、
曲名、歌曲 metadata、联网或训练环境。

`--degrees 2` / `--degrees 3` / `--degrees 4` 分别固定二、三、四次；
`--degrees 1 2 3 4` 自动比较四种阶数，默认也是这四种。`--alphas 0.1 1 10` 设置 ridge 候选，默认即这三个；`--alphas 0` 为普通最小二乘。
安装包后也可使用 `mairadar-constants`、`mairadar-regression`，或
`python -m mairadar.constants`、`python -m mairadar.regression`。

训练输入直接编辑 `src/mairadar/regression/training.py` 顶部的 `TRAINING_FEATURES`，
不通过 CLI 选列，也不加载额外配置文件。例如改用拆分统计量：

```python
TRAINING_FEATURES = (
    "note_density_mean", "note_density_std",
    "sweep_base_density", "sweep_motion_density", "slide_tricky_internal_mean",
)
```

仓库默认列表仍是原七个综合量。修改后照常执行 `fit --input ... --output ...`。
训练导出的 `model.json` 保存实际输入列及顺序；之后再改此列表不会改变已导出模型的推理。
统计量需要从事件 bundle 重新分析后才能得到，旧七维总表无法还原分布。

## 已有统计量与拟合候选

分析器在原七个综合量之外导出 46 个统计字段，共 53 个 raw 字段；使用模型时另加
`fitted_constant`。这些量已经可供训练选择，默认训练输入仍为原七维，并未验证多特征能降低误差。

| 字段前缀 | 新增数 | 样本口径 |
| --- | ---: | --- |
| `note_density_*` | 7 | 全谱固定 1.5 秒窗口的加权密度 |
| `sweep_*_density` | 3 | 选定爆发窗口的 base、motion、raw_motion 加权汇总 |
| `slide_tricky_internal_*`、`slide_tricky_launch_*`、`slide_tricky_total_load` | 15 | 各配置点的内部、启动干扰，以及整曲负荷和 |
| `slide_sequence_cadence_*`、`slide_sequence_concurrency_*`、`slide_sequence_length_*` | 21 | 各段的速度因子、并发压力、长度因子 |

分布统计为 count、mean、std、median、max、rms、p95。这里是 **RMS**（均方根），不是需要
目标值的 RMSE（均方根误差）。Peak、Jack、Slide Cumulate 目前仍只导出综合值。

第一轮可在 `TRAINING_FEATURES` 中试下面 16 个输入，保留 Peak、Jack、Cumulate 综合量，
拆开 Note、Sweep、Sequence；Tricky 综合量含配置修正，先与内部、启动分量一起保留：

```python
TRAINING_FEATURES = (
    "note_density_mean", "note_density_std", "note_density_p95", "peak",
    "sweep_base_density", "sweep_motion_density",
    "slide_tricky", "slide_tricky_internal_mean", "slide_tricky_internal_p95",
    "slide_tricky_launch_mean", "slide_tricky_launch_p95",
    "slide_sequence_cadence_p95", "slide_sequence_concurrency_p95", "slide_sequence_length_max",
    "jack", "slide_cumulate",
)
```

这只是待验证的候选，尚未替换默认列表。先比较一、二次回归：16 个输入二次展开为 152 项，
四次为 4,844 项；53 个输入全部四次展开为 395,009 项，明显超过当前官谱有效样本数。
比较时使用相同有效谱面和等级划分，在开发集 CV 中选择特征组合，留出集用于最终评估。

不要把所有派生量都堆入模型：`rms² = mean² + std²`，`note` 本身由密度 mean、std 组合，
`sweep = base_density + motion_density`；Tricky 的两套 count 相同，Sequence 的三套 count
也相同。count 和 total_load 还会随时长变化，必要时应优先尝试每秒频率或负荷率。

下一步值得补充、但本轮尚未导出的量：

- Jack：已有序列中的等效 BPM、主键数、打断数的分布，将速度和长度拆开。
- Slide Cumulate：已有段内负荷分布、段强度和有效长度，区分持续压力与少量峰值。
- Peak：已有候选窗口的 p95、峰值持续占比，区分整曲高强度与局部爆发；候选窗口会重叠，
  占比应按时间计算，不能把窗口数量当作独立物件数。
- 基础物件比例与动作速率：Tap/Hold/Touch/Slide、EX/Break，以及同时动作占比；沿用事件语义，
  不把 Slide 连接段重复当作新声明。它们需要新增汇总逻辑，成本高于直接导出已有中间量。

`fitted_constant` 用于展示和后续消费，不作为它自身的回归输入。

## 批量预测与 visualizer 导出

使用已导出的参数预测另一份 raw 结果，不需要 sklearn 或 NumPy：

```bash
python scripts/constant_regression.py predict \
  --input outputs/new-analysis/charts.csv \
  --model outputs/constant-fit-quartic/model.json \
  --output outputs/new-predictions.csv
```

官谱训练、自制谱推理也是这条流程：先用官谱定数总表执行 `fit`，再把冻结的模型用于自制谱。
例如已有自制谱 visualizer 时：

```bash
python scripts/constant_regression.py predict \
  --input outputs/fanmade-analysis \
  --model outputs/constant-fit-quartic/model.json \
  --output outputs/fanmade-predictions.csv
```

自制谱不需要定数标签，也不重新计算模型的均值、标准差或回归系数。官谱和自制谱的模型输入 raw
必须由同一版本、同一配置的分析器生成；这里的 `predict` 对应应用模型的 transform 步骤。

为已有 visualizer 数据生成带官方定数、拟合定数的新站点和 ZIP：

```bash
python scripts/build_pages.py \
  --site outputs/official-analysis \
  --constants-table outputs/constants.csv \
  --constant-model outputs/constant-fit-quartic/model.json \
  --cover-root data/raw \
  --output outputs/constant-preview
```

`--cover-root data/raw` 根据原始分析结果保留的 `sourceRef` 定位歌曲目录，重新附带 `bg.*` 曲绘，
不解析谱面、不重新计算 raw、分数或定数。用于给当前格式的无曲绘导出补充媒体。
文件名冲突时使用标题、难度编号、类型区分，例如 `Trust-5-dx.png`，
不覆盖另一首歌、不追加 hash。

已有完整 assets 的 visualizer 可以省略 `--cover-root`，直接复制随包曲绘。
`--no-covers` 仅在确实想省略封面时使用，与 `--cover-root` 互斥。
缺少 `sourceRef` 的发布版 ZIP 无法定位 raw，应改用原始分析 bundle。
原始目录也没有曲绘的谱面保留占位图。该命令只生成本地目录和 ZIP，所有图片都随包附带。
没有官方标签的谱面也能显示拟合值；调整雷达映射不影响拟合值。

新分析也可直接通过原 CLI 的 `--format visualizer --constants-table PATH --constant-model PATH`
附加定数。两项各自可选。

## scorer 的位置与雷达选轴

```text
analysis: 综合值 + stats
       ↓ 原始特征，不经过雷达映射
model.predict → 附加 fitted_constant
       ↓
scorer: 对配置项执行映射
       ↓
雷达从 mappedFeatures 独立选择维度
```

`fitted_constant` 与其他综合量一样是原始特征，可写入 CSV、做分布观察或作为雷达轴。
默认映射为 identity；在 mapping profile 的 dimensions 中添加该字段可将它映射至雷达尺度。
定数详情始终显示原始预测值，改 mapping profile 不改变预测。sklearn 内部 StandardScaler
属于模型预处理，与这里的 scorer 是两回事。禁止用 `fitted_constant` 训练或推理自身，避免循环依赖。

展示维度直接编辑 `src/mairadar/exporters/visualizer.py` 顶部的 `RADAR_FEATURES`。
默认 `None` 保留新导出的默认轴或已有站点的当前轴；改成元组后按其顺序选轴。
例如选择六个展示维度：

```python
RADAR_FEATURES = ("note", "peak", "sweep", "slide_tricky", "jack", "fitted_constant")
```

导出命令无需传选轴参数：

```bash
python scripts/mairadar.py --mode analysis_score --input data/parsed \
  --format visualizer --output outputs/selected-radar \
  --constant-model outputs/constant-fit-stats/model.json \
  --mapping-profile data/mapping_profile.json
```

`build_pages.py` 新分析和已有站点重新打包共用这份选择；已有站点也可同时传入
`--mapping-profile` 映射新量。scorer 映射仍定义在 `src/mairadar/scoring/config.py`
或现有 mapping profile 中，未配置 mapper 的统计量不能直接选成雷达轴。
站点的 `rawFeatures` 保留所有原始综合量、stats 和拟合结果，`mappedFeatures` 保留所有已映射值，
`rawScores` / `scores` 是选轴后的展示投影。导入时直接读取完整特征，不从投影回退补值。
CSV 和 analysis 模式现在也接受 `--constant-model`；预测在 scoring 之前由 pipeline 生成，
VisualizerExporter 只导出已有结果。

Play 纯内存调用可以依次使用：

```python
from mairadar.regression.derived import with_prediction

analysis = analyzer.analyze(parsed)
analysis = with_prediction(analysis, model)
scores = scorer.transform(analysis)
radar = [scores.features[name].value for name in selected_axes]
```

给自制谱导出 visualizer 时，将 `--site` 指向自制谱 bundle，使用官谱训练的
`--constant-model`，省略 `--constants-table` 即可。

## 匹配和来源

默认同时读取 [OTOGE DB 谱面页](https://otoge-db.net/maimai/lv/) 的
[`music-ex.json`](https://otoge-db.net/maimai/data/music-ex.json) 和同项目的
[`music-ex-deleted.json`](https://github.com/zvuc/otoge-db/blob/main/maimai/data/music-ex-deleted.json)。
两份数据统一匹配，不暴露地区或删除状态选项，也不据此拒绝定数。
`otoge-constants-2` 快照保存来源列表、原始记录、抓取时间及 HTTP Last-Modified；旧版已下载
的快照仍可读取。来源原文中的其他 metadata 不参与训练。

全来源先精确匹配曲名，找不到时才做 NFKC 归一化、移除末尾 `[DX]` / `[ST]` / `[STD]` / `[SD]`
并忽略标点、符号、空白及格式字符。保留文字内容与大小写，不使用编辑距离猜测。
纯符号曲名只能精确匹配，空 key 不会与其他符号曲名合并。

同名候选跨来源统一按已有 DX/SD、作者区分，类型只用于消除歧义。删除标题后缀不会修改
输入 metadata。作者忽略符号与空白差异，但不模糊匹配；仍不唯一则明确报歧义。
同一谱面在不同来源中都出现时按来源顺序使用首个明确值；某来源缺失值或难度时可以由
另一来源补充。输入行和同一来源内部的重复声明不会被去重。

难度 2/3/4/5/6 对应 BASIC/ADVANCED/EXPERT/MASTER/Re:MASTER。
仅明确的 `lev_*_i` / `dx_lev_*_i` 进入 `official_constant`。网站把 `13` / `13+` 回退显示成
`13.0` / `13.6` 的估值单独保存为 `display_constant`，不作为训练标签。

输入支持汇总 `charts.csv`、visualizer 目录或 `data/songs.json`、事件 bundle 及其根目录。
事件 bundle 只读取 metadata，不能凭空提供分析 raw；训练应使用已分析的结果。
总表保留输入字段，以及 `official_constant`、`match_status`、`matched_title`、`matched_artist`、
`matched_chart_type`、`title_match_method`、`display_constant`、`constant_value_kind`、来源 URL 和日期。

## 评估与无依赖推理

训练默认使用下面七个 raw 综合量，输入列表在 `training.py` 中修改。
推理始终按模型保存的列名和顺序取值：

```text
note, peak, sweep, slide_tricky, slide_sequence, jack, slide_cumulate
```

训练直接使用 sklearn 的 `Pipeline(StandardScaler, PolynomialFeatures, Ridge)` 和
`GridSearchCV`。多项式展开、标准化、岭回归求解、交叉验证和指标计算均由 sklearn 完成，
项目只保留输入表筛选、等级标签、参数导出与结果记录。
七个输入时，二次、三次、四次分别有 35、119、329 个非截距项，截距由 Ridge 单独拟合。

默认 seed=42，`train_test_split(..., stratify=level)` 划分约 80% 开发集和 20% 留出集，
`StratifiedKFold` 在开发集内生成等级分层的 CV 划分；不按歌曲分组。
没有 `level` 时使用官方定数的整数部分。单样本等级不能用于 sklearn 的分层二分，
因此只追加到开发集；当某等级少于折数时 sklearn 会提示部分折缺少该等级。
样本数、类别数和折数必须满足 sklearn 的划分条件。

整个 Pipeline 在每折训练部分拟合。GridSearchCV 按**各折 RMSE 的均值**选择参数，
不使用留出集选型；选型后评估留出集，再 clone 最优 Pipeline 并用全部有效标签重拟合部署模型。
报告保存 sklearn 版本、各等级数量、开发/留出行号和每折验证行号。
`predictions.csv` 的 `fitted_constant` 是部署模型输出；泛化误差看 `holdout_prediction` 和 `holdout`。

导出时直接读取 StandardScaler 的 `mean_` / `scale_`、PolynomialFeatures 的 `powers_`，
以及 Ridge 的 `coef_` / `intercept_`。输出仍为简单 JSON 参数，运行时公式不变，
不需要保存或加载 pickle/joblib，也不需要在 Play 中接入 sklearn。

```python
from mairadar.regression import PolynomialModel

model = PolynomialModel.load("outputs/constant-fit-quartic/model.json")
constant = model.predict({
    "note": 4.0, "peak": 6.0, "sweep": 0.5,
    "slide_tricky": 1.0, "slide_sequence": 2.0,
    "jack": 0.2, "slide_cumulate": 3.0,
})
```

也可将内存字典传给 `PolynomialModel(parameters)`。`runtime.py` 可单独复制使用，不依赖
sklearn、NumPy 或 mairadar 其他模块。模型 schema 为 `mairadar-polynomial-2`，输入数量由
features 列表确定。运行时只接受当前模型格式；已有 v1 模型需重新 fit。
站点格式统一为 `mairadar-visualizer-2`，旧站点需重新导出。公式为：

```text
z[j] = (raw[j] - center[j]) / scale[j]
constant = intercept + Σ(term.coefficient × Π(z[j] ** term.powers[j]))
```

使用双精度；不自动裁剪或取整，缺失值、NaN、Infinity、溢出报错。
模型保存训练输入范围；训练范围外的外推结果需单独评估。模型所用指标的算法或配置变更后需要重新训练。

## 一次性 metadata 修复

`repair_constant_metadata.py` 根据已核对的清单修复 raw metadata，默认只预览，`--apply` 执行。
它先校验全部预期值及曲绘路径、备份原始文件，再修改 raw 并生成附带曲绘的新 metadata bundle；保留 BOM、换行、
音符及七维 raw，不修改 parser，也不隐式重写其他 bundle。实际修复计划和备份保存在
`outputs/constant-regression-v50-3-repaired/`，涵盖 8 个标题、13 个类型，共 21 个原始文件。

## sklearn 训练验证

复用 `outputs/constant-regression-japan/constants.csv`，不重新抓取或分析曲库。
训练结果保存在 `outputs/constant-regression-sklearn/fit/`。首次改用 sklearn 后，随机划分由
其实现负责，行号与此前手写划分可能不同，因此不直接拿旧实验误差比较后端优劣。
此前的四次试验和带封面 visualizer 仍保留在 `outputs/constant-regression-level-stratified/`。

测试重点是导出公式与 sklearn 在新输入上的预测一致、留出集不影响预处理或选型、
输入与输出文件的往返，以及关闭第三方包后仍可推理。默认校准参数、临时映射版本号、
前端函数名和源码字符串不作为测试契约。
