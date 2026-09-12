# 架构

核心是可被调用的纯文本解析库；官方谱面分析只是一个使用场景，歌曲管理、去重和跨歌曲身份不属于解析器。

```text
inote 正文 → parser.parse_chart → ParseResult
maidata 文本 → parser.parse_text → ChartBundle 列表
文件 → io.parse_file → parser.parse_text
结果 + 调用方 metadata → io.write_bundle → 可选 CSV 目录
CLI → 文件适配器 + 导出器
```

mairadar.parser 的普通导入不加载 I/O 或 CLI；core、notes、durations、source 只处理文本、数值与模型。parse_chart 不接收源路径、不计算输入内容 hash、不生成歌曲 ID。

model.py 定义 Event、Diagnostic、ParseResult、Chart、ChartBundle；validation.py 提供不依赖存储的校验。ParseResult 没有歌曲 metadata；ChartBundle 是外围附加 metadata 的容器。

事件按动作开始时间与原文次序稳定排序，再从 1 连续编号；排序后同时更新头部引用。编号仅在单次结果中有意义，每次调用独立重置，不承诺跨谱面或跨文件稳定。原文位置承担审计定位，不用于歌曲身份。

io.py 读取文件、保存 CSV、解码 JSON 单元格，并可原样复制曲绘为包内附件。曲绘只通过 manifest 和外围 read_cover_path 暴露，不加入 Event、ParseResult 或解析必需参数。source_name 只在 metadata 中用于展示。导出目录 `<title>-<difficulty_index>-dx/sd` 不含 hash，不维护歌曲注册表、输入缓存或去重索引。CSV hash 只校验文件完整性；不属于解析 API。目录替换是外围文件写入事务，不能扩展成歌曲管理逻辑。

后续 analysis/scoring 读取事件对象或已导出结果；正常分析不得再次扫描 Simai 来计数。baseline、难度权重和映射算法不进入 parser。当前尚未实现这些模块。

秒时间从第一槽开始，音频 offset 独立；分拍不是拍号，BPM 变化不重置拍相位。物件跨窗口仍是一个事件，窗口只构造视图。Slide 声明时间与滑动区间分别保留，无头路径也不例外。

未知物件产生局部诊断；时序失效时停止后续时间扫描并标记不完整。模型完整性、支持范围、CSV 往返与独立上游对照是不同验证层次。测试与固定参考信息见 SYNTAX_SUPPORT.md，详细字段见 SCHEMA_PROPOSAL.md。
