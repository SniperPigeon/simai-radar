# 七维二次定数回归

`regression_beta` 从子特征引入前的 `4804bb9` 起步，保留 sklearn 训练，并移植预测定数作为
标量特征、scorer 后处理和可选雷达维度。完整子特征实现保存在 `constant_regression` 分支；
实验定义、全部结果和取舍见[消融报告](REGRESSION_ABLATION.md)。

## 训练设计

固定输入为未经过 scorer 的七维综合量：

```text
note, peak, sweep, slide_tricky, slide_sequence, jack, slide_cumulate
```

训练使用 `Pipeline(StandardScaler, PolynomialFeatures(degree=2, include_bias=False), Ridge)`，
共 35 个非截距项。多项式展开、缩放、回归求解和模型选择均由 sklearn 完成，
不提取子特征分布，不提供选特征或选阶 CLI。

seed 默认 42，约 80% 开发集、20% 留出集，按展示等级分层，不按歌曲分组。
开发集内五折 CV 从 alpha `[0.1, 1, 10]` 中按平均 RMSE 选择正则强度。
单样本等级留在开发集，极少等级不足五折时 sklearn 会提示部分折缺少该等级。
整个 pipeline 在各折训练部分拟合；选型后评估留出集，再用全部有效标签重拟合部署模型。

## 定数总表和训练

需要 Python 3.11+。采集、推理只用标准库，训练额外安装 sklearn：

```bash
python -m pip install -e '.[regression]'

python scripts/constants.py fetch --output data/constants/otoge-japan.json
python scripts/constants.py match \
  --input outputs/official-analysis \
  --snapshot data/constants/otoge-japan.json \
  --output outputs/constants.csv

python scripts/constant_regression.py fit \
  --input outputs/constants.csv \
  --output outputs/constant-fit-quadratic
```

本机可用 `/opt/anaconda3/bin/python`。输入可以是含七维 raw 的 CSV 或当前版本的 visualizer。
事件 bundle 只有 metadata，需先分析才能提供 raw。所有输出使用新路径。
`match` 有未匹配行时写出完整表并返回 1，确认统计后再运行 fit；无明确标签的行不进入训练。
已有七维 `constants.csv` 可以直接重拟合，无需重新分析或抓取。

`--alphas`、`--seed`、`--folds` 分别设置正则候选、随机种子、交叉验证折数。
默认即上面的实验设置；二次多项式在训练代码中固定。

| 文件 | 用途 |
| --- | --- |
| model.json | 全标签重拟合后的部署参数 |
| evaluation_model.json | 仅开发集训练，用于复现留出误差 |
| report.json | 参数选择、划分、误差、基线和排除统计 |
| predictions.csv | 输入行、部署预测、留出预测及状态 |
| test_vectors.json | 模型输入顺序及预期输出 |

没有额外 export 步骤，复制 model.json 即可。拟合定数不作为自身输入。

## 官谱训练、自制谱推理

```bash
python scripts/constant_regression.py predict \
  --input outputs/fanmade-analysis/charts.csv \
  --model outputs/constant-fit-quadratic/model.json \
  --output outputs/fanmade-predictions.csv
```

自制谱无需定数标签；应用冻结的均值、标准差和系数。输入必须由相同口径的分析器生成。
推理不需要 sklearn、NumPy、网络、歌曲 metadata 或训练数据：

```python
from mairadar.regression import PolynomialModel

model = PolynomialModel.load("outputs/constant-fit-quadratic/model.json")
constant = model.predict({
    "note": 4.0, "peak": 6.0, "sweep": 0.5,
    "slide_tricky": 1.0, "slide_sequence": 2.0,
    "jack": 0.2, "slide_cumulate": 3.0,
})
```

也可把参数字典传给 `PolynomialModel(parameters)`。`runtime.py` 可单独复制到调用方。
运行时按模型的 features 列表确定输入顺序，使用双精度，不自动裁剪或取整：

```text
z[j] = (raw[j] - center[j]) / scale[j]
constant = intercept + Σ(coefficient × Π(z[j] ** powers[j]))
```

缺失输入、NaN、Infinity 或溢出明确失败。参数格式为 `mairadar-polynomial-2`，无旧模型迁移层。

## 拟合定数、scorer 和雷达

```text
analysis：七维 raw
    ↓ model.predict(raw)
附加 fitted_constant（原始定数）
    ↓ scorer
已映射字段
    ↓ 独立选轴
雷达
```

`fitted_constant` 是普通标量 FeatureResult，进入 analysis 输出、CSV 和 visualizer；
没有 stats 子字段。它默认 identity 映射，可在现有 mapping profile 的 dimensions 中配置自己的
rawAnchors/t4Max。定数详情始终显示原始预测，雷达显示映射值；scorer 不参与回归输入。

在 `src/mairadar/exporters/visualizer.py` 修改展示列表即可，例如六维：

```python
RADAR_FEATURES = ("note", "peak", "sweep", "slide_tricky", "jack", "fitted_constant")
```

默认 None 保留七个基础轴；重新打包已有站点时保留其当前轴。
改轴只影响展示投影，rawFeatures 和 mappedFeatures 保留所有原始量与映射结果。
所选轴必须有 scorer 输出；选择 fitted_constant 时需要提供模型或使用已含预测的站点。

```bash
python scripts/mairadar.py --mode analysis_score --input data/parsed \
  --format visualizer --output outputs/constant-preview \
  --constant-model outputs/constant-fit-quadratic/model.json \
  --constants-table outputs/constants.csv \
  --mapping-profile data/mapping_profile.json
```

`--constant-model` 也用于 CSV 导出和 analysis 模式；`--constants-table` 只用于 visualizer 标签。
两项独立可选。已有当前格式站点可重新打包：

```bash
python scripts/build_pages.py --site outputs/official-analysis \
  --constant-model outputs/constant-fit-quadratic/model.json \
  --constants-table outputs/constants.csv \
  --output outputs/constant-pages
```

build_pages 与新导出共用 RADAR_FEATURES；已有站点也可传 mapping profile，在预测后重映射。
`--cover-root data/raw` 可按 sourceRef 补曲绘，不能和 `--no-covers` 同时使用。
站点格式为 `mairadar-visualizer-2`；不从旧展示投影回退推断完整原始值。

服务纯内存调用只需：

```python
analysis = analyzer.analyze(parsed)
analysis = with_prediction(analysis, model)
scores = scorer.transform(analysis)
radar = [scores.features[name].value for name in selected_axes]
```

`with_prediction` 来自 `mairadar.regression.derived`。输入解析、分析、预测、映射和导出保持独立。

## 日本定数匹配

统一读取 OTOGE DB 的日本 music-ex.json 和 music-ex-deleted.json，不按地区或删除状态筛选。
先精确曲名，再做 NFKC、末尾 DX/ST/STD/SD 后缀及标点符号归一化；不使用编辑距离。
同名候选按已有类型、作者消歧；仍不唯一就报告歧义。

只有明确的 lev_*_i / dx_lev_*_i 数值作为训练标签。网页由 13/13+ 回退显示的 13.0/13.6
单独保留为 display_constant，不进入训练。某来源缺值时可由另一来源补充。
原文定位、metadata 和曲绘只在输入、匹配及导出层处理，不成为 parser 或预测的必要依赖。
