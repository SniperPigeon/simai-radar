# Play 接入：可验证的实施路径

此文记录 2026-09-17 的首轮 .NET 实测、2026-09-18 的目标 fork 核对，以及
2026-09-21 完成的首版 C# 适配层。探针不是
“已经与 Play 兼容”的声明：实测对象是
NuGet `Lingfeng-bbben.MajSimai` **2.2.2**，而 [handoff](CSHARP_HANDOFF.md) 中的静态参考是
Play `c3423a4` 锁定的 MajSimai `fdb2a3e`。接入用户的 Play fork 时，以 fork 的实际
MajSimai 版本和 `NoteLoader` 行为为准；不要把 NuGet 的最新版本同时放进已有 MajSimai 的 Unity 工程。

## 已确认的目标 fork

同级目录 `../MajdataPlay` 的 `origin` 为 `https://github.com/SniperPigeon/MajdataPlay.git`，
当前分支 `dev`、HEAD `79abbd1dc6ac98272b950d75900eedfe7872c878`，工作树干净。
Play 参考 commit `c3423a4` 是其直接父提交；对接相关的 `GamePlayManager.cs`、
`NoteLoader.cs` 和 MajSimai gitlink 与参考 commit 无差异，新增提交仅把游戏版本号
从 2.0.1 改为 2.0.2。`ProjectSettings/ProjectVersion.txt` 是 Unity `6000.3.17f1`，
`apiCompatibilityLevel: 3` 对应 .NET Standard，Unity 此配置支持 .NET Standard 2.1。

fork 的 `Assets/Plugins/MajSimai` 是 Git submodule，固定 commit
`fdb2a3e39d8997a0abbf8b4679062d854473cc77`；当前尚未初始化（`git submodule status`
显示前缀 `-`）。该 commit 的 `MajSimai.csproj` 声明 package version `2.2.1`，不是
探针安装的 NuGet `2.2.2`。已在仓库外用 .NET SDK 独立编译固定 commit 的
`netstandard2.1` 目标，0 warning / 0 error。正式适配与测试应引用此 submodule pin，
无需在 Unity 工程再安装 NuGet MajSimai；进入 Play 构建之前才初始化 submodule。

## 已运行的格式探针

仓库的 [`integrations/majsimai-probe`](../integrations/majsimai-probe/) 是独立 .NET 10
控制台程序，只用于观察 NuGet 的公开类型和解析结果，不是 Unity 运行时代码：

```sh
dotnet run --project integrations/majsimai-probe -- --schema
dotnet run --project integrations/majsimai-probe -- res/examples/majsimai_probe_fumen.txt
```

输入是单张谱面的 **inote 正文**，不是整个 `maidata.txt`。第二个命令使用小型合成样例，
包含前导休止、同头双分支、真正无头 Slide、变 BPM、Touch 和尾部空槽。进程打印 JSON；
这只是检查结构的诊断输出，不是核心分析器的 JSON 依赖。

实测 `SimaiChart.NoteTimings` 与 `CommaTimings` 都是 `ReadOnlySpan<SimaiTimingPoint>`；
每个 timing point 有 `Timing`、当前 `Bpm`、`RawContent`、`RawTextPosition`、`Notes` 等字段。
物件有 `Type`、`StartPosition`、`TouchArea`、`HoldTime`、`SlideStartTime`、
`SlideTime`、修饰符和 `RawContent`。样例中声明于 0.5 秒的 Slide 的
`SlideStartTime=1`、`SlideTime=0.5`；最后一个 comma timing 为约 2.1667 秒，
而最后的 Touch 在 1.5 秒。前者可用来核对谱面结束，不能用后者替代。

这次样例还有一个易错点：同头第二分支和真正的 `?` 无头 Slide 的
`IsSlideNoHead` 都为 true。适配时必须结合 timing point 的原始分组来判断是否共享
一个显式头；不能单凭该布尔值决定 `head_event_id`。此外，点上的 `Bpm` 是生效值，
不是每一条 `(BPM)` 声明的独立记录；`RawContent` 也没有展开成 Slide 路径段。

## 已实现的首个里程碑

[`csharp/`](../csharp/) 现已包含 `netstandard2.1` 的 `SimaiRadar.Core` 和
`SimaiRadar.MajSimaiAdapter`，以及可脱离 Unity 运行的 .NET 10 xUnit 测试。默认开发构建
引用 NuGet 2.2.2；通过 `MajSimaiProject=/absolute/path/to/MajSimai.csproj` 可让同一份源码
和测试直接引用 Play 固定 submodule，避免把第二份 MajSimai 放进 Unity。

迁移期真实谱差分已对 NuGet 2.2.2 和目标 Play pin `fdb2a3e` 各运行同一批 24 张
`inote_5`，共 20,747 个事件；物件语义、拍轴、总时间及 Slide 几何全部通过，最大秒误差
`4.35e-7s`。差分不包含 `bar_count`／连接段逐段时间；这些由 C# 适配器内冻结的标准
prefab 长度表负责。差分也尚不
包含未移植的 C# 七维 raw。可重复运行的工具及边界见
[`csharp/README.md`](../csharp/README.md)。

适配器只把 `NoteTimings[].Notes` 当作物件来源，不重新扫描原始 inote。拍轴由相邻
`CommaTimings` 的实际秒差乘该区间 BPM / 60 得出，并在边界吸附到有理分拍；
`SignatureNumerator/Denominator` 不是分拍，不能用于此计算。Slide 路径仅解释
MajSimai 已输出的 `SimaiNote.RawContent`；标准 prefab bar 数在适配层冻结，不需要 Unity 注入。

运行方法、Play 薄层接法及当前歧义边界见 [`csharp/README.md`](../csharp/README.md)。

## 运行时分层

```text
Play 已有的 MajSimai 版本 / 文本
    → MajSimaiAdapter（唯一了解 MajSimai 类型的层，补足语义）
    → RadarChartInput（一次解析结果内的标准事件和完整时长）
    → RadarAnalyzer（七维 raw；无 Unity、歌曲目录、媒体、UI 依赖）
    → PolynomialModel（可选，读取 model.json；输入只能是七维 raw）
    → ScoreTransformer（独立映射）
    → Play 薄调用层（谱面加载后缓存结果，UI 只读取）

C# 离线训练工具 → 训练数据校验 → 标准化／二次展开／Ridge → model.json
```

核心项目目标为 `netstandard2.1`，以便在普通 .NET 测试程序和已确认的 Unity
`6000.3.17f1` 中复用。探针可继续用本机 .NET 10；**不能**把 `net10.0` 的探针程序集
当作 Unity 插件。目标 Play 已使用 MajSimai 源码 submodule，核心的 DLL/asmdef 引入方式
可在正式接入时确定。

`RadarChartInput` 应等价于当前 [events-0.3](SCHEMA_PROPOSAL.md) 的分析必需语义：
事件种类、开始／结束秒与拍、Slide 声明时间、显式头关系、路径段与 `bar_count`、
修饰符、完整谱面结束时间和最后物件结束时间。原文 Unicode 定位不是分析必需字段，可以
不移植；原来依靠 `source_start` 做同刻排序和无头分组的分析，改用 C# 解析边界给出的
局部声明顺序／组序号。同头分组仍需单次结果内的局部声明身份，不是歌曲 ID。内存中使用类型化对象；CSV/JSON 只用于
离线对照。调用方只在外围加一次音频 offset。若类型化输出与适配边界无法对齐，
记录日志并跳过本谱分析，不能悄悄猜一个可评分的值；不要求输出结构化语法诊断。

## 适配层可行性结论

**能实现全 C#，并且不改 MajSimai 源码。**但 MajSimai 公开的类型化结果单独不足以
无损构造当前分析输入。已核对 Play `c3423a4` 的 MajSimai submodule pin `fdb2a3e`，
这些关键公开字段与 NuGet 2.2.2 探针一致。推荐把补充逻辑放在一个 C# 适配边界：

1. `SimaiChart.NoteTimings` 和 `Notes` 是唯一的物件来源；`CommaTimings` 提供空槽与
   谱面尾部时间。使用折叠前数组，重复同位置声明不去重。
2. 不扫描 `SimaiChart.Fumen`。按 `CommaTimings` 的实际秒时间和区间 BPM 建立有理数拍轴；
   float 结果优先吸附到容差内的最简分拍，否则取允许分母内的最近分拍。重复同值 BPM
   声明无法从类型化输出还原，但不会改变时间、拍相位或当前分析结果。
3. 对齐每个 comma 的时间与生效 BPM、每个 note timing 的时间及物件数量／顺序。
   `SimaiNote.RawContent` 只由单独的 Slide 路径段解释器处理；遇到无法对齐的输入就
   记日志并跳过本谱，不回退成猜测事件。
4. 路径 bar 数使用适配层冻结的 MajdataPlay 标准 prefab 长度；连接段按 bar 数比例分配
   原有 `SlideTime`。Play 不注入 resolver，也不做运行时一致性检查。

这不复刻 Simai 的时间或物件 parser，也不修改 MajSimai submodule。MajSimai 自身对部分
无效物件可能静默跳过：按用户当前要求，
适配层只记录可见异常或不一致日志，不承诺覆盖全部语法错误，也不将“无日志”称为完全兼容。
m
| 问题 | 已确认的来源 | 可执行方案 |
| --- | --- | --- |
| BPM 声明、重复同值 BPM、分拍与拍轴 | `CommaTimings` 给出生效 BPM 和秒时间；`Signature*` 不是分拍 | 用相邻实际秒差 × 区间 BPM / 60 还原拍差并吸附为有理分拍；不扫描 `Fumen`。省略不可见的重复同值 BPM 事件，不影响分析结果 |
| Hold/Touch/Slide 秒时间、尾部休止 | `NoteTimings`、`HoldTime`、`SlideStartTime`、`SlideTime`、`CommaTimings` | 直接映射；最后 comma timing 与最后物件结束分别保存，校验 EOF 和跨尾持续物件 |
| 同头分支、无头 Slide、重复声明 | `IsSlideNoHead` 对同头后续分支和 `?`/`!` 无头都为 true | 从 timing `RawContent` 的 `/` 与 `*` 分组边界给每个原始声明分配局部组序号；按数组顺序对齐 Notes，绝不按位置去重 |
| Slide 路径段、`bar_count`、连接段时间 | `RawContent` 保留路径；标准 prefab 长度属于固定游戏规则 | 在 C# 适配层拆分路径并引用冻结 bar-count 表；连接段按 bar 比例分配，不按段数平均 |
| 失败物件和不完整状态 | MajSimai 的 `TryGetSingleNote` 失败会写 `Debug.WriteLine` 并跳过 | 不改 MajSimai；记录能发现的异常和对齐失败。无法保证发现其内部所有静默丢弃，不能声称完整语法覆盖 |

保持 Play 现有 MajSimai 实例：不要把 NuGet 2.2.2 再装进已有 submodule 的 Unity
工程。NuGet 探针只用于确认 API 形状；真正适配与测试统一引用目标 Play pin。

### 适配层仍需补足的实测缺口

| 所需语义 | NuGet 2.2.2 直接可见信息 | 处理与验证 |
| --- | --- | --- |
| 音符和持续秒时间 | `NoteTimings`、`Notes`、`HoldTime`、`SlideStartTime`、`SlideTime` | 直接映射；验证声明和实际启动的口径、重复物件不去重 |
| 完整谱面时长 | `CommaTimings` | 核对 EOF、尾部休止和跨尾部持续物件；另存 `last_event_end_s` |
| BPM 点与精确拍位置 | timing point 的当前 `Bpm` 和实际秒时间，未逐条暴露声明 | 对区间秒差做 BPM 归一化并吸附到有理分拍；不能使用 `Signature*`，也不能把绝对 float 秒数直接当拍 |
| Slide 头/分支/无头 | `IsSlideNoHead`、timing point 分组、`RawContent` | 在同一声明组内建立一次头、多个路径；真正无头不造虚拟头 |
| Slide 路径与连接段 | 物件 `RawContent`，无结构化路径和 `bar_count` | 只在适配边界解释此字段；bar 数引用冻结的标准 prefab 长度并据此分配逐段时长 |
| 日志与失败边界 | MajSimai 的异常及调试日志；完整性并未保证 | 公开入口返回失败结果而不向 Play 抛异常；Play 记录日志并隐藏可选雷达，不把成功返回当成完全兼容 |

若这些补充会改变七维特征口径，必须重新生成训练输入并拟合模型，不能继续套用旧系数。

## 不打开 Unity 的独立测试

1. **核心单元测试**：将手工构造的 `RadarChartInput` 喂给 `RadarAnalyzer`，逐维覆盖正常
   行为、窗口边界、重复同位置声明、跨窗口持续物件、Slide 多分支和无头声明时间。
   `dotnet test` 即可运行；这些测试不引用 MajSimai 或 Unity。
2. **适配集成测试**：用目标 Play pin 的 MajSimai 解析小型 inote，再适配。先断言标准
   事件与人工 golden；另测上述缺口。不能用当前 Python 解析结果代替人工或独立上游真值。
3. **迁移期差分测试**：同一标准事件临时比较 Python/C# 七维 raw，区分算法移植错误；
   这只是迁移辅助，不是最终测试依赖。最终保留的是人工 golden、已知回归和 C# 独立
   行为测试，不靠复制 Python 当前输出定义预期值。
4. **C# 训练／推理／scorer 测试**：用小型人工训练集独立验证标准化、35 项二次展开、
   Ridge 解、模型序列化、推理和映射次序；现有 model.json / test_vectors.json 如存在，
   只作迁移期对照。C# 训练工具生成最终 model.json，不再调用 sklearn/Python。
5. **Play 冒烟测试**：最后才在 fork 中加载一张 Master/Re:Master 小谱，检查计算只在
   加载后进行一次、切谱重新计算、UI 读取缓存；错误时记日志并不显示虚假雷达。

首个可验收里程碑是目标 Play pin 的 `MajSimai → RadarChartInput` 在小型样例上通过，
并能脱离 Unity 运行测试。之后再逐项移植七维、训练/推理和 scorer，不必先建完整插件框架
或全曲库流程。所有 C# 测试独立通过后，才移除 Python 源码和工具；不在尚无替代实现时
提前删除现有成果。
