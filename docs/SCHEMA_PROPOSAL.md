# 事件格式 events-0.2

状态：已获用户确认。解析器接收文本，输出单次解析范围内的物件、时间与诊断，不生成歌曲身份、不管理歌曲、不做去重。0.2 移除 chart_id 和身份哈希，event_id 改为递增整数；不兼容旧版 CSV 表头。

## 核心结果与导出边界

`parse_chart(text)` 返回 `ParseResult`：events、diagnostics、complete、chart_end_time_s、last_event_end_s。它不要求曲名、难度、DX/SD、文件路径或文件 hash。

`parse_text(text, difficulties=...)` 是可选的 maidata 文本适配，返回多张 `ChartBundle`，在事件外附带 metadata。文件读取属于 `mairadar.io.parse_file`，目录和批量执行属于 `mairadar.cli`。

导出时每张难度谱面一个子目录：

```text
output/
  <title>-<difficulty_index>-<dx|sd>/
    events.csv
    charts.csv
    diagnostics.csv
    manifest.json
    cover.jpg          # 可选，扩展名随原图格式
```

目录名不附加 hash 或自动后缀，不作为解析核心的歌曲 ID。DX/SD 优先使用明确的 cabinet/cabinate 元数据或调用方指定；缺失时可由语法解析后的独立检测器按约定物件规则补全，不从曲名或文件夹猜测。语法 parser 本身不检测类型。文件名非法字符替换为下划线，原始 title 不改。缺少必要的导出 metadata 时由外围报错；不会影响只解析文本的 API。

## events.csv：22 列

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| event_id | int | 单次结果按最终输出顺序从 1 连续编号，包含 BPM 事件 |
| kind | enum | tap / hold / touch / touch_hold / slide / timing |
| is_slide_head | nullable bool | Slide 头的 Tap 为 1，其他物件为 0，timing 留空 |
| timing_type | nullable enum | timing 当前仅 bpm；物件留空 |
| slide_declare_time_s | nullable float64 | 所有 Slide 必填声明时间，有头/无头都保留；其他类型留空 |
| start_time_s | float64 | 动作开始；Slide 为实际滑动开始 |
| end_time_s | float64 | 动作结束；Tap/Touch/timing 等于 start |
| start_beat | rational string or null | 动作开始在全局四分音符拍轴上的位置，例如 7/2 |
| end_beat | rational string or null | 动作结束的拍位置，由完整 BPM 映射换算 |
| bpm | nullable float64 | BPM 事件的新值，有限正数；物件留空 |
| position | nullable string | 外圈 "1"–"8"；Touch "A1"/"B1"/"C" 等；timing 留空 |
| head_event_id | nullable int | 同次结果中的头部事件编号；无头 Slide 和其他类型留空 |
| slide_path_json | JSON array or null | Slide 有序路径段数组，其他类型留空 |
| is_break | nullable bool | 物件自己的 BREAK，头与路径独立；timing 留空 |
| is_ex | nullable bool | 物件自己的 EX；timing 留空 |
| is_mine | nullable bool | 物件自己的 mine；timing 留空 |
| flags_json | JSON object or null | 额外修饰符；物件无额外标记用 {}，timing 留空 |
| raw_token | string | 来源原文；同头多路径衍生行可以共享 token |
| source_start | int | 原文 Unicode code point 起点，0-based |
| source_end | int | 原文半开终点，不是 UTF-8 字节数 |
| source_line | int | 原文行号，1-based |
| source_column | int | 原文 code point 列号，1-based |

排序为精确 start_time、source_start、同源衍生顺序；完成排序后编号并重映射 head_event_id。非法 token 被跳过时最终 ID 不留空洞；每次调用、每张难度谱面都重新从 1 编号，不承诺跨版本或跨谱面稳定。歌曲身份若有需要，由调用方 metadata 持有。

一个普通 Slide 输出一个头 Tap 和一个路径事件；同头多路径只输出一个头，路径各占一行；连接 Slide 的各段在同一行内联。无头 Slide 不生成虚拟头，但 slide_declare_time_s 不能为空。头自身只使用 start/end；有头路径的声明时间等于所关联头的 start_time_s。

同时间同位置的多个声明不能去重。事件行数包含显式头和 timing，不能冒充游戏官方物量。duration_s 为 end-start 的派生量，不重复存储。

## Slide 内联段

每段对象为：

```json
{
  "shape": "-",
  "start_position": "1",
  "via_position": null,
  "end_position": "5",
  "start_time_s": 1.5,
  "end_time_s": 2.0,
  "raw_segment": "-5[4:1]",
  "time_resolution": "explicit_duration"
}
```

单段也是长度 1 的数组。数组顺序代表连接顺序；V 保留途经点，其他形状不伪造途经点。位置编码与主表一致，C1/C2 源别名统一为 C，原写法仍在 raw_token 中。

固定 MajdataPlay 的连接段时间会按 prefab bar 数分配，包含源文本逐段给时长的情况。解析器保留整个 Slide 时间，连接段时间为 null、time_resolution=needs_geometry；该可选派生数据未计算不影响 complete，不按段数平均分配。K 等不支持形状明确报诊断。

## metadata、诊断和 manifest

`charts.csv` 每目录一行：source_name（可选，由文件适配器附加）、chart_type（dx/sd/空）、difficulty_index、difficulty_label、level_text、title、artist、designer、offset_s、chart_end_time_s、last_event_end_s、audio_duration_s、metadata_json。不生成 chart_id 或源内容身份 hash；未知原始 metadata 保留。

`diagnostics.csv`：severity、code、message、source_start、source_end、source_line、source_column、raw_text、recovery_json。无需 chart_id，由同一结果/目录确定归属。

`manifest.json` 是可选 CSV 导出的描述：schema/parser 版本、固定参考 commit、title/difficulty_index/chart_type、来源说明、表名、行数、complete/status。CSV SHA-256 仅用于导出文件完整性校验，不参与任何解析身份、去重或目录命名，解析核心不计算 hash。可选的 assets.cover 记录包内 cover.jpg/jpeg/png/webp 的相对文件名；附件一同纳入 file_sha256 校验，曲绘不进入事件或纯文本解析结果。

## 时间与空值

- 秒时间相对谱面第一槽，不包含音频 offset；前导休止保留。offset 由调用方应用一次。
- BPM 声明输出 timing/bpm 点事件，包含初始和重复同值声明；重复同值不改变物件时间、拍相位或分析结果。
- 分拍和槽只在解析内部推进时间，不导出独立时间表；分拍不是拍号。
- Hold 的 start/end 为按住区间，Slide 的 declare/start/end 分开。持续长度在声明 BPM 下确定，动作拍位置按完整 BPM 映射换算。
- 文本结束、最后物件结束、音频长度分别保存；last_event_end_s 排除 timing，无物件则为空。
- 内部使用 Fraction，输出有限 float64，不反复截断小数。beat 存约分整数字符串或分数字符串。
- UTF-8 CSV；空单元格为 null，布尔 0/1；event_id/head_event_id 读取为整数，position/beat 保持字符串。
- JSON 单元格使用标准 CSV quoting，内存中直接为 list/dict。
- 源位置相对去 BOM 后、未转换 CRLF 的 Unicode 文本。C# 的 UTF-16 偏移不能直接当 code point 偏移比较。

未知物件跳过整个来源 token 并记录诊断；无法确定后续时间则停止该谱面扫描。无法确定的时间/拍不猜值，complete=false。EOF 显式支持但不插入额外逗号或时长。

人工 golden 位于 `res/examples/schema_v0.2/Schema Prototype-5-sd/`，不是解析器生成文件。源输入对应 7 行结果（1 BPM + 6 物件），共享头为 event_id=4，两个路径的 head_event_id 都为 4。实现及调用见 PARSER.md，实际语法支持见 SYNTAX_SUPPORT.md。
