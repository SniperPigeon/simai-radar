# 谱面定数采集与回归

定数采集、训练和推理均独立于 visualizer，也不进入 parser、analysis 或 scoring。
输入是调用方提供的 metadata 和七维 raw 值；不重新扫描 Simai，不重算曲库。
核心与推理只用 Python 标准库，训练额外需要 NumPy：

```bash
python -m pip install -e '.[regression]'
```

以下 `python` 需要 Python 3.11+。也可使用安装后的 `mairadar-constants` 和
`mairadar-regression` 替代对应脚本。所有输出使用新路径，不覆盖已有结果。

## 1. 定数快照与 bundle 总表

```bash
python scripts/constants.py fetch \
  --region jp \
  --output data/constants/otoge-jp.json

python scripts/constants.py match \
  --input outputs/visualizer-v50-3 \
  --snapshot data/constants/otoge-jp.json \
  --output outputs/constants.csv
```

采集 [OTOGE DB 谱面页](https://otoge-db.net/maimai/lv/) 实际使用的
[`music-ex.json`](https://otoge-db.net/maimai/data/music-ex.json)；`--region intl`
则使用 `music-ex-intl.json`。快照保存地区、来源 URL、抓取时间和 HTTP Last-Modified。
这是第三方站点收录的官方谱面定数，不是 SEGA 官方接口。页面会在 `_i` 缺失时从显示
等级计算近似数值；本实现不使用这个回退，只接受明确的 `lev_*_i` / `dx_lev_*_i`。

`--input` 可以是：

- analysis/scoring 导出的 `charts.csv` 或所在目录；保留其中的 raw 列。
- visualizer 的目录或 `data/songs.json`；将 `rawScores` 转成相同的 raw 列。
- 单个事件 bundle 或由直接子目录组成的 bundle 根目录；只读 `charts.csv` metadata，
  不读事件、不分析。该表仅有定数，训练应使用带七维 raw 的分析结果重新匹配。

规则是先逐字符匹配 `title`，不 trim、不做 Unicode 归一化、不删除 `[DX]` / `[ST]`
后缀、不模糊匹配。来源中该曲名只有一种谱面类型时，不要求 bundle 的类型一致；只有
同名存在多个候选时才用明确类型筛选，`sd` / `std` / `ST` 视为标准谱。类型不能消除
歧义时留空，不再用艺术家、等级或其他字段猜测。选定谱面类型后才匹配难度，缺少该
难度不会回退到另一类型。

难度编号：2=BASIC、3=ADVANCED、4=EXPERT、5=MASTER、6=Re:MASTER；1/7 等不推定定数。
每个输入行都保留，包括重复声明。新增字段：

| 字段 | 含义 |
| --- | --- |
| `official_constant` | 明确定数；不可用时为空 |
| `match_status` | `matched`、`title_not_found`、`ambiguous_type`、`ambiguous_title`、`type_not_found`、`unsupported_difficulty`、`difficulty_not_found`、`constant_missing`、`invalid_constant` |
| `matched_chart_type` | 实际命中的来源类型，保留原有 `chart_type` 不变 |
| `constant_source` / `constant_region` | 标签来源及地区 |
| `constant_fetched_at` / `constant_source_updated_at` | 抓取时间与来源记录日期 |

`match` 有任一未命中就返回 **1**，同时完整写出总表并打印各状态计数；因此不要用 `&&`
盲目串联匹配与训练。检查总表后，可以明确选用匹配成功的行训练。

## 2. 多项式回归与评估

```bash
python scripts/constant_regression.py fit \
  --input outputs/constants.csv \
  --output outputs/constant-fit
```

固定使用七维原始值，顺序为：

```text
note, peak, sweep, slide_tricky, slide_sequence, jack, slide_cumulate
```

训练前排除未精确匹配、非 `ok` 输入、缺少/非有限 raw 的行，记录排除原因。
不把缺失值补零，不用 `level` 当标签或第八个特征。训练表必须来自同一地区和标签快照。
缺少标签是有记录的训练筛选；坏输入被跳过时，即使其他行完成训练，CLI 仍返回非零。

默认比较 degree=1/2/3、ridge alpha=0.1/1/10，共九组。可通过 `--degrees`、`--alphas`
缩小实验范围，例如 `--degrees 2 --alphas 1`；`--alphas 0` 为普通最小二乘。
二次包含七个一次项及 28 个平方/交互项，另有不正则化的截距。
原始值按训练子集的均值与总体标准差标准化，常量列 scale 设为 1。
用增广最小二乘和 SVD 求解 ridge，避免直接求正规方程的逆。

评估使用固定 seed=42（`--seed` 可改）：按**精确曲名**分组，同曲的 DX/SD、不同难度
及重复行始终在同一组。先留出 20% 曲名；其余部分默认五折分组交叉验证，以 RMSE 选型。
每折单独计算标准化参数。选型后才评估留出集，最后在全部有效标签上重拟合部署模型。
至少需要六个有完整特征与标签的曲名。

产物：

| 文件 | 用途 |
| --- | --- |
| `model.json` | 全部有效标签重拟合后的部署参数 |
| `evaluation_model.json` | 只在 development 集拟合，复现留出集误差 |
| `report.json` | 候选 CV 指标、曲名分组、留出集 MAE/RMSE/R²、均值基线、训练误差、排除统计与来源 |
| `predictions.csv` | 全部输入行、部署拟合值、预测状态、排除原因、评估分组、留出预测 |
| `test_vectors.json` | 七维输入和双精度预测值，便于独立推理实现做数值对照 |

`predictions.csv` 的 `fitted_constant` 是最终部署模型的输出；评估应看
`holdout_prediction` 和 `report.json` 的 `holdout`，不能把重拟合后的训练误差当泛化误差。
模型没有自动裁剪或四舍五入，超出训练 raw 范围的输入可能产生不可靠的外推结果。
模型还记录训练 raw 与标签范围；宿主可以据此决定展示方式。

## 3. 无第三方依赖推理

```python
from mairadar.regression import PolynomialModel

model = PolynomialModel.load("outputs/constant-fit/model.json")
raw = {
    "note": 4.0, "peak": 6.0, "sweep": 0.5,
    "slide_tricky": 1.0, "slide_sequence": 2.0,
    "jack": 0.2, "slide_cumulate": 3.0,
}
constant = model.predict(raw)
```

也可直接把字典参数传给 `PolynomialModel(parameters)`，无需文件。宿主必须传入全部
七维成功的 raw 结果；不需要曲名、歌曲 metadata、mapping_profile、联网或 visualizer。
`src/mairadar/regression/runtime.py` 可以单独复制使用，既不依赖 NumPy，也不依赖
mairadar 的其他模块。其数学接口是：

```text
z[j] = (raw[j] - center[j]) / scale[j]
constant = intercept + Σ(term.coefficient × Π(z[j] ** term.powers[j]))
```

`schema_version = mairadar-polynomial-1`，`features` 冻结输入顺序，`input_kind = raw`。
每个 term 显式保存七个非负整数指数，所有计算使用双精度。读取时验证 schema、特征
顺序、有限数值、正 scale、唯一项及 degree 1–3；缺失值、NaN、Infinity 和溢出报错。
Play 后续接入只需按这个契约求值；本轮不提供 C# 实现。
七维分析算法或配置变化后，应重新导出 raw 并训练，不能仅因为维度名称相同就复用旧模型。

对已有分析输出批量预测，不需要任何标签：

```bash
python scripts/constant_regression.py predict \
  --input outputs/new-analysis/charts.csv \
  --model outputs/constant-fit/model.json \
  --output outputs/new-predictions.csv
```

## 4. Visualizer 接入

新分析可通过现有 CLI 添加参数：

```bash
python scripts/mairadar.py --mode analysis_score \
  --input data/parsed --format visualizer --output outputs/with-constants \
  --constants-table outputs/constants.csv \
  --constant-model outputs/constant-fit/model.json
```

这两个参数各自可选；没有官方标签的谱面也可预测。页面显示官方定数及来源、拟合定数，
缺失值显示 `—`。拟合使用导出时的 raw，与 visualizer 中调整的雷达映射无关。
`ConstantTable` 查询的是已匹配的 bundle 总表，因此附加时按原始 title、难度和类型精确
定位；总表重复键存在歧义时不会合并或任选一行。

复用已有 visualizer，无需重新分析：

```bash
python scripts/build_pages.py --site outputs/visualizer-v50-3 \
  --constants-table outputs/constants.csv \
  --constant-model outputs/constant-fit/model.json \
  --output outputs/constant-preview --no-covers
```

会生成新目录及 ZIP。`--no-covers` 可省略；启用它时不复制曲绘，已有的曲绘导出诊断仍
保留在 JSON 中，但不会阻止无曲绘导出。分析失败依然拒绝打包。这个命令只生成本地产物。

## 本次官方 bundle 试拟合

2026-09-17 的 JP 快照，复用 `outputs/visualizer-v50-3` 的现有七维 raw，
来源路径为 `AstroDX-raw`，不重新解析或分析。产物在 `outputs/constant-regression-v50-3/`。

- 输入 1,886 张 Master/Re:Master；匹配 1,569 张，排除无标签的 317 张。
- 308 张精确曲名未命中，6 张所选类型缺少对应难度，1 张同名同类型仍有歧义，
  1 张找不到指定类型，1 张缺少明确定数。带 `[DX]` / `[ST]` 后缀的不同曲名保持未命中。
- 选中 degree=2、alpha=10；319 张留出谱面的 MAE=0.2864、RMSE=0.3762、R²=0.7623。
- 常数均值基线在同一留出集的 MAE=0.6167、RMSE=0.7718。
- 最终模型对全部 1,886 张谱面输出拟合值；未命中定数的 317 张也保留预测。

这些结果只描述本次数据、匹配规则、特征版本及切分；并不代表所有难度或自制谱面的精度。
