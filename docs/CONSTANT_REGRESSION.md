# 日本谱面定数与回归 CLI

定数采集、训练、推理独立于 visualizer，也不进入 parser、analysis 或 scoring。
统一使用日本定数：两份日本数据自动一起读取，不配置 region，不按删除状态筛选、分组或展示。
匹配到明确的定数就纳入训练；没有定数或只有网页估值的谱面保留记录但不训练。

需要 Python 3.11+。训练需要 NumPy，采集和推理只依赖标准库：

```bash
python -m pip install -e '.[regression]'
```

本机可直接使用 `/opt/anaconda3/bin/python`；系统自带的 `python3` 是 3.9，不满足项目要求。
下列命令在仓库根目录运行。输出必须使用新路径，避免覆盖已有实验。

## 从采集到导出

```bash
# 1. 一次抓取全部日本定数来源
python scripts/constants.py fetch --output data/constants/otoge-japan.json

# 2. 为已有七维 raw 结果生成总表，不重新分析谱面
python scripts/constants.py match \
  --input outputs/constant-regression-v50-3-repaired/metadata-bundle \
  --snapshot data/constants/otoge-japan.json \
  --output outputs/constants.csv

# 3. 固定三次多项式并自动导出模型
python scripts/constant_regression.py fit \
  --input outputs/constants.csv \
  --output outputs/constant-fit-cubic \
  --degrees 3
```

`match` 存在未匹配行时会写出完整总表并返回 1，所以这几步分开运行。
确认匹配统计后，`fit` 会自动筛选可训练行。`fit` 的输出目录中已经包含：

| 文件 | 用途 |
| --- | --- |
| `model.json` | 全部有效标签重拟合后的推理参数，可直接导出给调用方 |
| `evaluation_model.json` | 只在 development 集拟合，用于复现留出集误差 |
| `report.json` | 候选参数、分组、MAE/RMSE/R²、基线和排除统计 |
| `predictions.csv` | 所有输入行的拟合定数、状态、排除原因和留出预测 |
| `test_vectors.json` | 固定七维输入及预期双精度输出，供其他推理实现对照 |

**没有额外的 export 步骤。** 训练完成后复制 `model.json` 即可；模型推理不需要 CSV、
曲名、歌曲 metadata、联网或训练环境。

`--degrees 2` 固定二次，`--degrees 3` 固定三次；`--degrees 1 2 3` 自动比较三种阶数，
默认也是这三种。`--alphas 0.1 1 10` 设置 ridge 候选，默认即这三个；`--alphas 0` 为普通最小二乘。
安装包后也可使用 `mairadar-constants`、`mairadar-regression`，或
`python -m mairadar.constants`、`python -m mairadar.regression`。

## 批量预测与 visualizer 导出

使用已导出的参数预测另一份 raw 结果，不需要 NumPy：

```bash
python scripts/constant_regression.py predict \
  --input outputs/new-analysis/charts.csv \
  --model outputs/constant-fit-cubic/model.json \
  --output outputs/new-predictions.csv
```

官谱训练、自制谱推理也是这条流程：先用官谱定数总表执行 `fit`，再把冻结的模型用于自制谱。
例如已有自制谱 visualizer 时：

```bash
python scripts/constant_regression.py predict \
  --input outputs/visualizer-fanmade-4 \
  --model outputs/constant-fit-cubic/model.json \
  --output outputs/fanmade-predictions.csv
```

自制谱不需要定数标签，也不重新计算模型的均值、标准差或回归系数。官谱和自制谱的七维 raw
必须由同一版本、同一配置的分析器生成；这里的 `predict` 对应应用模型的 transform 步骤。

为已有 visualizer 数据生成带官方定数、拟合定数的新站点和 ZIP：

```bash
python scripts/build_pages.py \
  --site outputs/constant-regression-v50-3-repaired/metadata-bundle \
  --constants-table outputs/constants.csv \
  --constant-model outputs/constant-fit-cubic/model.json \
  --output outputs/constant-preview --no-covers
```

`--no-covers` 省略曲绘；需要保留曲绘时，`--site` 应指向具有对应 assets 的完整 visualizer。
上面的 metadata-bundle 是只修正 metadata 的轻量输入，使用它时保留 `--no-covers`。
该命令只生成本地目录和 ZIP。没有官方标签的谱面也能显示拟合值；调整雷达映射不影响拟合值。

新分析也可直接通过原 CLI 的 `--format visualizer --constants-table PATH --constant-model PATH`
附加定数。两项各自可选。

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
事件 bundle 只读取 metadata，不能凭空提供七维 raw；训练应使用已分析的结果。
总表保留输入字段，以及 `official_constant`、`match_status`、`matched_title`、`matched_artist`、
`matched_chart_type`、`title_match_method`、`display_constant`、`constant_value_kind`、来源 URL 和日期。

## 评估与无依赖推理

固定七维 raw 顺序：

```text
note, peak, sweep, slide_tricky, slide_sequence, jack, slide_cumulate
```

每次仅用训练子集计算均值和总体标准差；常量列 scale=1。二次有 35 个非截距项，
三次有 119 个非截距项，包括所有交互项。截距不正则化，ridge 通过增广最小二乘和 SVD 求解。

默认 seed=42，按匹配到的来源曲名分组，同曲的不同写法、DX/SD、难度和重复行位于同一组。
旧表没有 `matched_title` 时才回退到输入曲名。先留出 20% 曲名，其余默认五折交叉验证，
按 CV RMSE 选参数；选型完成后评估留出集，再用所有有效标签重拟合 `model.json`。
至少需要六个可训练曲名。`predictions.csv` 的 `fitted_constant` 是重拟合模型输出；
泛化误差应看 `holdout_prediction` 和报告中的 `holdout`，不能用训练误差代替。

```python
from mairadar.regression import PolynomialModel

model = PolynomialModel.load("outputs/constant-fit-cubic/model.json")
constant = model.predict({
    "note": 4.0, "peak": 6.0, "sweep": 0.5,
    "slide_tricky": 1.0, "slide_sequence": 2.0,
    "jack": 0.2, "slide_cumulate": 3.0,
})
```

也可将内存字典传给 `PolynomialModel(parameters)`。`runtime.py` 可单独复制使用，不依赖
NumPy 或 mairadar 其他模块。模型 schema 为 `mairadar-polynomial-1`，公式为：

```text
z[j] = (raw[j] - center[j]) / scale[j]
constant = intercept + Σ(term.coefficient × Π(z[j] ** term.powers[j]))
```

使用双精度；不自动裁剪或取整，缺失值、NaN、Infinity、溢出报错。
模型保存训练输入范围；训练范围外的外推结果需单独评估。七维算法或配置变更后需要重新训练。

## 一次性 metadata 修复

`repair_constant_metadata.py` 根据已核对的清单修复 raw metadata，默认只预览，`--apply` 执行。
它先校验全部预期值、备份原始文件，再修改 raw 并生成新的 metadata bundle；保留 BOM、换行、
音符及七维 raw，不修改 parser，也不隐式重写其他 bundle。实际修复计划和备份保存在
`outputs/constant-regression-v50-3-repaired/`，涵盖 8 个标题、13 个类型，共 21 个原始文件。

## 本次二次／三次对比

使用已缓存的日本数据重新封装为 `data/constants/otoge-japan-20260917.json`，沿用原始抓取时间。
输入为修复后的 1,886 张谱面；匹配 1,690 张明确定数，176 张未命中、20 张仅估值。
新总表和对比模型位于 `outputs/constant-regression-japan/`。

| 模型 | ridge alpha | 留出集 MAE | 留出集 RMSE |
| --- | --- | --- | --- |
| 二次，degree-2 | 10 | 0.278390 | 0.363040 |
| 三次，degree-3 | 10 | 0.278999 | 0.362965 |

两次实验的训练与 341 张留出谱面完全相同。三次在留出集上与二次基本持平，两个模型各自
导出到对应子目录；没有用留出集调整参数。
