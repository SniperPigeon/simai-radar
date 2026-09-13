# Simai 语法范围与实现状态

parser 0.3.0 / schema events-0.3 已实现。下表的 supported 表示通过本项目合成输入、错误诊断和时间/事件 golden 验证；**不等于已经通过 Unity 或 .NET 运行时差分测试**。

语义依据是 MajdataPlay commit `c3423a4bba536e53921e8fdedab2b9d91121b393` 锁定的 MajSimai `fdb2a3e39d8997a0abbf8b4679062d854473cc77`。缓存源码的 blob SHA-1 已与固定 commit 的 Git tree 核对；未将源码复制进本仓库。引用及 blob 信息见 `res/reference/upstreams.json`。

| 语法 | 实际状态与边界 |
| --- | --- |
| `&key=value`、多行 `&inote_N` | supported；难度编号保留，未知 metadata 保留。标量字段只读取声明行；同值重复记录 info，不影响完整性。冲突重复、异常续行明确诊断，带难度编号的字段只影响对应难度；后值覆盖前值 |
| title/artist/des/des_N/lv_N/first | supported；offset 独立；first_N 作为本项目显式支持的难度覆盖项优先于 first |
| UTF-8 BOM、CRLF、Unicode | supported；source 范围相对去 BOM 后、未转换换行的 Unicode 文本，单位为 code point |
| `||` 普通注释 | supported；注释中的逗号不推进时间；保留原文定位 |
| `||s` 拍号扩展 | 正整数 `||s 分子/分母` 仍 unsupported，complete=false；不合法的指令及 some pattern / solips 等普通注释直接忽略，不改变时间或拍号 |
| `(120)`、小数 BPM、变 BPM | supported；每次声明是 timing/bpm 事件，重复同值保留源身份，但不改变物件时间或拍相位 |
| `{4}`、正整数/小数分拍 | supported；未声明分拍时按固定上游默认 4，不猜 BPM；大分母不自动改写 |
| `{#seconds}` | supported；须先有 BPM，按参考转换成 `240/(BPM*seconds)` 的分拍。之后只改 BPM 会改变槽秒长，直到重新声明分拍 |
| `,` 与空槽 | supported；空槽不输出事件，但时间推进和尾部时长保留 |
| `/` 同时押、`12` | supported；恰好两位裸数字展开，不无限泛化。`12/3` 也展开，属于明确的便利扩展；上游只在整组恰好两位时展开 |
| 反引号伪同时押 | supported；每组增加 `1.875/BPM` 秒，不改变下一个逗号槽时间；空组明确报错，区别于上游 RemoveEmptyEntries |
| Tap `1`、`b/x/m` | supported；重复同位置声明不去重 |
| `$`、`$$` | supported；force_star/fake_rotate 放入 flags，不改变事件类型 |
| Hold/TouchHold `h` | supported；重复 h 按同一个布尔标志处理，保留原 token，不增加物件；短形式时长为 0，与固定参考一致，不补微小非零时长 |
| Hold `[division:count]`、`[#seconds]`、`[bpm#division:count]` | supported；比例为整数 division/count，division>0、count>=0；无效表达式报错，不回退成 0 |
| Touch A/B/C/D/E，`f`、BREAK/EX/mine | supported；位置统一为 A1/B1/C 等。C1/C2 源别名输出 C，raw_token 保留原写法；不接受任意 C 后缀 |
| Slide `- ^ v < > V p q pp qq s z w` | supported；保留形状、V 途经点与端点，检查固定播放器的端点约束；导出 prefab bar_count 长度单位，不冒充物理距离或完整运动轨迹 |
| 同头多 Slide `*` | supported；一个显式头 Tap，每条路径一行，共享 head_event_id；分支时间和修饰符独立 |
| `?` / `!` 无头 Slide | supported；不生成头，但保留 slide_declare_time_s；两种标记保存到 flags.no_head_marker；按固定参考，两者都保留等待时间 |
| `@` Tap-head Slide | supported；头为 Tap，is_slide_head=true，flags.tap_head=true |
| Slide 头/路径的 `b/m` | supported；路径前标记归头，路径中仅紧邻 `[` 或 token 末尾的 b 设置 BREAK，其余 b 直接忽略，与固定 NoteFlag.Detect 一致；m 的非法位置仍诊断。标记不传播到其他分支 |
| Slide `x` | supported 为头部 EX；路径 is_ex=false；无头/后续分支的头修饰保留到 suppressed_head_flags，不伪造额外头 |
| `c` | supported 为 flags.using_sv=false（参考默认启用 SV，c 关闭），不误写为 true |
| Slide `[division:count]`、`[bpm#division:count]`、`[bpm#seconds]` | supported；custom BPM 同时确定等待时间。`[#seconds]` 仅适用于 Hold，不是该参考的合法 Slide 表达式 |
| Slide `[wait##division:count]`、`[wait##seconds]`、`[wait##bpm#division:count]` | supported；wait 是相对声明时刻的秒等待量，不是全谱绝对时间，允许 0；数字不带字面 s 单位 |
| 连接 Slide：末尾总时长或每段时长 | supported；路径子数组、整条等待/时长已解析。多组时长求和，首个显式等待/custom-BPM 决定等待；混合指定方式明确报错 |
| 连接 Slide 逐段时间 | supported；固定 NoteLoader 按 prefab bar 数分配时间（包含逐段给时长的写法），每段导出 bar_count 和连续 start/end；不按段数平均分配 |
| Wifi 作为连接段 | rejected；固定播放器不允许 |
| `<HS*...>` / `<SV*...>` | unsupported；UNSUPPORTED_SPEED，明确标记不完整；不假装已保存其播放效果 |
| `K` 自定义 Slide | unsupported；保留定位诊断，不编造路径或输出一个看似完整的 Slide |
| `E` / EOF | supported；槽首（可带时间指令）的独立 E 终止当前 inote，后续内容直接忽略并记录 TRAILING_CONTENT info，后续难度照常解析。E1–E8 Touch 与数值科学计数法不作终止符；仅末尾独立小写 e 兼容为 E。EOF 记录 info，不插入额外槽时长 |
| 未知 token / 非法持续时间 | error；跳过整个来源 token（`*` 组原子处理），保留可确定的其他物件，complete=false |
| 未知或非法时间指令、缺失 BPM | error；停止该谱面后续时间扫描，chart_end_time_s 留空，不能确定的拍字段留空 |

## 验证边界

`tests/test_parser.py` 覆盖人工 prototype、跨小节重复计数、注释逗号、重复 BPM、Touch、持续物件跨变速、各时长表达式、共享头、无头声明时间、逐段路径、Unicode 源位置、局部递增 ID、纯文本入口、CSV 往返、替换回滚与批量退出策略。真实谱面只在 data/raw 中作抽样验证，不进入提交测试。

所有支持声明基于上述 Python 测试及固定源码静态核对。当前环境未提供 dotnet，尚未构建或运行 MajSimai 导出对照程序。数值计算采用 Fraction 而非复刻上游 float32 的累积误差；持续秒数在声明 BPM 下解析，动作拍位置在完整 BPM 映射上换算。
