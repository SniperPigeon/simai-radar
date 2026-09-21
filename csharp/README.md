# MajSimai → RadarChartInput

这一目录是 Play 接入的第一段可独立验证代码，不依赖 Unity：

```text
MajSimai（唯一物件来源）
  → MajSimaiChartAdapter
  → RadarChartInput（分析器输入）
```

`SimaiRadar.Core` 只放类型化分析输入；`SimaiRadar.MajSimaiAdapter` 是唯一引用
MajSimai 的项目；测试项目通过真实 MajSimai 解析小型 inote，再断言适配结果。

## 本地测试

默认开发配置使用 NuGet `Lingfeng-bbben.MajSimai` 2.2.2：

```sh
dotnet restore csharp/SimaiRadar.slnx
dotnet test -m:1 --disable-build-servers /p:UseSharedCompilation=false /nr:false \
  csharp/SimaiRadar.slnx
```

也可以对 Play 固定的源码项目运行完全相同的测试，不安装第二份 MajSimai：

```sh
dotnet restore csharp/SimaiRadar.MajSimaiAdapter.Tests/SimaiRadar.MajSimaiAdapter.Tests.csproj \
  -p:MajSimaiProject=/absolute/path/to/MajSimai.csproj
dotnet test --no-restore -m:1 --disable-build-servers \
  /p:UseSharedCompilation=false /nr:false \
  -p:MajSimaiProject=/absolute/path/to/MajSimai.csproj \
  csharp/SimaiRadar.MajSimaiAdapter.Tests/SimaiRadar.MajSimaiAdapter.Tests.csproj
```

`-m:1` 和关闭 build server 只是让受限环境中的构建更稳定，业务代码不依赖这些参数。

## 真实谱迁移对照

[`scripts/compare_majsimai_actual.py`](../scripts/compare_majsimai_actual.py) 会将选定
`maidata.txt` 的同一张 inote 分别交给 MajSimai 和迁移期 Python parser。它比较物件种类、
位置、开始／结束秒、吸附后的拍、修饰符、Slide 路径几何、谱面结束及最后物件结束；同刻
事件按语义作为多重集合比较，不要求两个实现的内部数组顺序相同。

2026-09-21 已用 NuGet 2.2.2 和 Play 固定源码 `fdb2a3e` 分别抽测 24 张真实
`inote_5`，共 20,747 个事件，结果均为 24/24 通过。最大事件秒时间差为
`4.35e-7s`；其他大多数样本在 `1e-12s` 数量级。

此工具刻意不比较 `bar_count` 和连接段逐段时间，因为它直接比较 MajSimai 与 Python 共同
暴露的语义；C# 适配器自身使用冻结的 MajdataPlay 标准 prefab 长度表。七维 raw 使用下文的
分析差分工具另行比较。Python 差分只用于迁移排错，不成为最终运行或验收依赖。

## 时间与拍轴

适配器不重扫原始 inote。事件只来自 `SimaiChart.NoteTimings[].Notes`，完整谱面时间来自
`CommaTimings`。全局拍轴用相邻实际秒差和该区间 BPM 计算：

```text
beat_delta = (right_time_s - left_time_s) * left_bpm / 60
```

结果在适配边界吸附到有理分拍。先选择容差内分母最小的分拍；若没有，则选择允许分母
范围内误差最小的分拍，避免 MajSimai 的浮点累积误差污染后续精确的 `1/2` 拍等判断。
最大分母和优先容差是适配器内部固定策略，不成为 Play 设置项。

Slide 路径只解释 MajSimai 已输出的 `SimaiNote.RawContent`，不会从原文制造额外物件。
标准 shape→prefab 归一化和 bar count 已冻结在适配程序集内，连接段按这些游戏固有长度
比例分配总时长；Play 不需要注入 resolver，也不进行运行时一致性校验或报警。

## 接入 Play

1. 初始化 Play 已固定的 `Assets/Plugins/MajSimai` submodule；不要再给 Unity 工程安装
   NuGet 2.2.2。
2. 让 Core 和 Adapter 源码/程序集编译时引用这份 submodule。当前项目通过
   `MajSimaiProject` MSBuild 属性完成同一件事。
3. Play 已有 MajSimai 完成解析后调用一次 `Adapt(existingChart)`，成功时把
   `RadarChartInput` 交给分析器并缓存；切谱时重算。
4. `AdaptationResult.IsSuccess=false` 时按 Play 可选功能惯例记录日志并隐藏雷达，不抛出
   影响主游戏的异常，也不生成猜测分数。
5. 音频 offset 仍由 Play 外围只加一次；适配器和分析器都使用谱面相对时间。

MajSimai 会把“同头后续分支”和显式 `?`/`!` 无头 Slide 都表示为
`IsSlideNoHead=true`。适配器可处理普通同头分支和独立无头 Slide；若它们在同一 timing、
同一位置混合而 MajSimai 输出无法无歧义区分，则返回适配错误。这里不修改 MajSimai，也
不猜测错误的 `head_event_id`。

## 分析、拟合与公开接口

`SimaiRadar.Analysis` 已移植固定顺序的七维：`note`、`peak`、`sweep`、
`slide_tricky`、`slide_sequence`、`jack`、`slide_cumulate`。每一维独立失败，不会抛出
影响调用方的异常，也不会用零值冒充成功结果。

Sweep 按当前正式 `SweepBurstAnalyzer` 默认路径拆成四个可独立审核的层：

- `SweepRecognizer`：从 Tap/短 Hold 生成按拍批次，识别可变宽 Sweep 主干；
- `SweepFamilyBuilder`：建立相邻 Sweep 组的时间关系；
- `SweepHandMotion`：用双手动态规划计算位移、换手和过快跳跃；
- `SweepBurstAnalyzer`：生成基础/动作负荷，选三个不重叠的两秒窗口并聚合。

Python 参考实现的三个文件共约 2472 行；C# 正式路径约 1361 行。压缩来自不移植当前未启用的
成对 Sweep、旧全曲聚合和实验参数分支，不合并或重写会改变正式 raw 的识别规则。

`SimaiRadar.Regression` 将 regression-beta 模型写成简单静态常量：固定七维输入顺序、
7 个 center、7 个 scale、intercept 和 35 个二次系数。运行时不读取 model.json；8 个冻结
Python 测试向量用于验证 C# 双精度推理。

`SimaiRadar.Runtime.RadarRuntime` 是对外入口：

```csharp
var runtime = new RadarRuntime();

// Play 首选：复用已经解析的 SimaiChart。
RadarComputationResult result = runtime.Analyze(existingSimaiChart);

// 独立调用方可直接给 inote。
RadarComputationResult parsed = await runtime.ParseAndAnalyzeAsync(inote);

// 也可直接分析标准事件。
RadarComputationResult fromEvents = runtime.Analyze(radarChartInput);
```

结果包含 `ChartInput`、固定七维 `Analysis.Features`、可选 `FittedConstant` 和 `Errors`。
只有七维全部成功才运行拟合；显示选轴不参与模型输入。正常输入现在返回 `Status=ok` 和
`FittedConstant`；任一维失败时仍返回 `partial` 且不生成不完整预测。

### 真实谱七维差分与错误边界

迁移工具 [`scripts/compare_csharp_analysis_actual.py`](../scripts/compare_csharp_analysis_actual.py)
可对真实 `inote_5` 比较 Python 与 C# 七维。脚本同时做两种检查：独立 parser 的端到端
结果允许 MajSimai 单精度 BPM 带来的 `1e-6` 以内时间误差；Sweep 还会把 C# 适配后的同一份
事件交给 Python 参考算法，以 `1e-8` 检查纯算法移植，避免用 parser 容差掩盖实现差异。

错误边界分三层：

- MajSimai 抛出的解析异常由 `ParseAndAdaptAsync` 转成失败结果；
- 非正 BPM、固定几何不支持的 K Slide、无法无歧义建立头关系等适配错误返回
  `ChartInput=null`、`Analysis=null` 和错误文本；
- 单个维度的数据错误只使该维失败，其他维度继续，整体状态为 `partial`。

MajSimai 对部分非法 token（实测包括普通 `bad` 和未闭合 duration）会静默跳过。适配层在
“不重解析原始 Simai、不修改 MajSimai”的约束下无法可靠发现这类输入；因此无错误结果不代表
完整语法校验。Play 只应隐藏雷达并记录可见失败，不能让可选模块中断游戏。
