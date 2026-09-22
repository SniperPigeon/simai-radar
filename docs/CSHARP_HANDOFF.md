# C# / MajdataPlay 接入 handoff

## 背景与目标

把当前 `regression_beta` 的七维分析、定数预测和 scorer 移植到 C#，最终接入用户 fork 的
MajdataPlay。最终交付的解析适配、七维分析、模型训练/推理、scorer 和测试工具均为 C#，
不保留 Python 运行链路。现有 Python 仅在迁移期间充当公式和历史输出参考，C# 独立验收后
再移除旧实现。正式解析复用 Play 使用的 MajSimai。目标 fork 已在同级目录核对；首版
`MajSimai → RadarChartInput` C# 适配层和独立测试已经位于 [`csharp/`](../csharp/)；
七维分析已全部移植。固定七维二次模型已作为 C# 常量加入，公开 `RadarRuntime` 在七维成功时
返回 fitted constant；任一维失败时返回 partial 且不预测。
已完成 NuGet 格式探针、固定上游源码核对和同一测试套件验证。

子特征实验没有显示明确的泛化收益，完整实现留在 `constant_regression` 分支，结果见
[消融报告](REGRESSION_ABLATION.md)。beta 已恢复标量分析接口，不包含 stats/summary 特征层。

当前设计：

```text
MajSimai 输出 → C# 语义适配层 → 七维 raw → 二次模型预测
                                 └─ 加上 fitted_constant → scorer → 雷达选轴 / Play UI
```

- 七维：note、peak、sweep、slide_tricky、slide_sequence、jack、slide_cumulate。
- C# 离线训练工具实现 StandardScaler → 二次多项式展开 → Ridge，共 35 个非截距项。
- C# 运行时直接引用固定 center、scale、intercept 和 35 个二次系数做双精度推理；拟合定数
  作为第八个可选标量输出，再经过 scorer 映射。
- 雷达展示轴独立选择，不能改变模型输入。歌曲 metadata、曲绘、文件发现由 Play 管理。
- 主要面向 Master/Re:Master 自制谱。现有训练集已排除不明确的定数标签，但“只训练定数 ≥12”尚未实现。

## 建议开发顺序

1. 已确认同级目录 `../MajdataPlay` 是用户 fork 的 `dev` 分支，HEAD `79abbd1d`；
   Unity `6000.3.17f1`、MajSimai submodule pin `fdb2a3e`，与既有参考 pin 相同。
   当前 submodule 尚未初始化，正式构建前需初始化；版本核对见
   [Play 接入路径](PLAY_INTEGRATION.md)。固定参考见 [upstreams.json](../res/reference/upstreams.json)。
2. 已使用 C# 探针、NuGet 2.2.2 和目标 Play pin 验证 MajSimai 输出。适配层不扫描
   `SimaiChart.Fumen`：按 `CommaTimings` 的实际秒差和区间 BPM 还原并吸附有理拍轴，
   只解释 `SimaiNote.RawContent` 的 Slide 路径；bar 数引用适配层冻结的标准 prefab 长度。
   不修改 MajSimai 源码，无法无歧义分组时不生成分数。核心使用内存类型化事件。
3. 七维分析器已完成。Sweep 只移植正式默认路径，并拆为识别、族连接、双手 DP、两秒窗口
   四层；未把未启用的成对 Sweep 和实验参数分支带入 Play。迁移差分同时比较独立 parser
   端到端结果，以及把同一适配事件交给 Python/C# Sweep 的纯算法结果。
   Sweep 的候选历史、候选数、family 连接和选集状态均有预算；冲突选集使用连通分量和显式栈，
   不依赖递归深度。`CancellationToken` 从 Play 薄入口贯穿到高复杂度循环，取消返回数据状态。
4. 固定模型的 C# 求值已完成并由冻结向量核验；后续若仍需要在 C# 内重新训练，再移植离线
   StandardScaler / PolynomialFeatures / Ridge 工具和 scorer。核心保持无 Unity 依赖，验证后
   作为独立程序集或包接入 Play，在谱面加载后计算并复用结果，UI 只消费结果。

## 最需要先确认的语义

- 拍位置、BPM 变化、完整谱面时长及首尾休止；不能只用最后一个物件代替谱面结束时间。
- Slide 声明、实际启动、结束三个时间，头与分支归属，无头 Slide 和连接段结构。
- 连接段时间和 bar_count 涉及 Play 的规则，不能假设 MajSimai 单独输出就已包含全部信息。
- 重复声明保留，持续物件和连接段不重复计数；offset 由调用方只加一次。
- 当前 Python 使用 Fraction 处理拍边界，C# 的浮点与边界判定需要专门对照。

缺少的信息应在解析/适配边界一次性补齐，分析器不各自重新扫描 Simai。只使用 MajSimai
已解析的物件作为物件来源；路径解释不独立制造物件。MajSimai 对部分非法物件可能只写
调试日志并跳过，接受不提供完整诊断的边界，不将成功返回解释为已验证全谱语法。当前适配
测试已对目标 Play pin 独立运行，七维已有 C# 行为测试和有限真实谱对照；仍不能把 Python 输出
无条件当成播放器真值。若最终 C# 的特征口径
发生变化，要重新生成训练输入并拟合，不能直接沿用旧系数。

## 参考入口

- 分析公式：[ANALYSIS.md](ANALYSIS.md)，代码 `src/mairadar/analysis/features/`。
- 事件语义：[SCHEMA_PROPOSAL.md](SCHEMA_PROPOSAL.md)；验证边界：[SYNTAX_SUPPORT.md](SYNTAX_SUPPORT.md)。
- 回归与 scorer 次序：[CONSTANT_REGRESSION.md](CONSTANT_REGRESSION.md)。
- 无依赖公式：`src/mairadar/regression/runtime.py`；映射：`src/mairadar/scoring/`。
- 本地七维二次模型及测试向量：`outputs/regression-beta/fit/`，这些生成产物未纳入 Git。

首个里程碑是“固定版本 MajSimai → 标准事件 → 七维对照”跑通。先保持核心、适配和对照程序简单，
不用搭插件框架，也不用把 Python 解析器整套翻译到 C#。

2026-09-17 补充：已用 .NET 10 对 NuGet MajSimai 2.2.2 运行独立输出探针，发现
`NoteTimings`/`CommaTimings` 可读，但显式 BPM 声明和 Slide 路径段不是现成的标准事件。
实测与下一步验收见 [Play 接入路径](PLAY_INTEGRATION.md)；此探针版本不等于目标 Play pin。
