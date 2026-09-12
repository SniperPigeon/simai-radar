# 解析器调用

Python 3.11+，仅标准库。解析核心面向收到的文本；目录、歌曲信息和批量导出属于可选外围工具。

## 播放器或其他程序直接调用

```python
from mairadar.parser import parse_chart

result = parse_chart("(120){4}1,1?-5[4:1],E")
print(result.complete)
for event in result.events:
    print(event.event_id, event.kind, event.start_time_s)
```

返回一个 ParseResult，包含 events、diagnostics、complete、chart_end_time_s、last_event_end_s。无需输入/输出路径、曲名、难度、DX/SD 或歌曲 ID；没有文件读写、身份 hash 或去重。event_id 按结果顺序从 1 编号，head_event_id 为同次结果内的整数引用，每次调用重新编号。

若需要传输 JSON，可用 `dataclasses.asdict(result)` 得到普通 dict/list；slide_path_json 在内存中已是数组。此 API 是 Python 调用入口，尚未包装成 Unity/.NET 可直接加载的 ABI 或服务。

重复 h、x 等布尔修饰符不会重复生成物件；物件位置、时长和剩余语法仍严格检查，raw_token 保留原文。仅末尾独立小写 e 兼容为结束符 E，并记录 info；其他未知 token 仍导致 incomplete，不静默丢物件后继续评分。

处理完整 maidata 文本时使用 `parse_text(text, difficulties=[5, 6])`，返回 list[ChartBundle]，其中 chart 保存文本 metadata。parse_chart 则直接接收单个 inote 的正文。

## 文件适配与导出

```python
from mairadar.io import parse_file, write_bundle, read_bundle, read_cover_path

bundle = parse_file("path/to/maidata.txt", difficulties=[6])[0]
# 只有导出需要 title、difficulty_index 和 chart_type。
# 若文本没有 &cabinet=DX/SD，可由调用方明确填写：
# bundle.chart.chart_type = "sd"
directory = write_bundle(bundle, "data/parsed", cover_path="path/to/bg.jpg")
restored = read_bundle(directory)
cover = read_cover_path(directory)  # Path 或 None
```

parse_file 已移到 mairadar.io，不属于 mairadar.parser 的公共入口。它只读 UTF-8 文本、调用 parse_text，并在 metadata 附加 source_name；缺少 title 时用文件 stem 作导出标题。路径不参与事件编号，核心不限制调用方的 source_name。

纯正文解析后若要导出，可以自行构造 ChartBundle：

```python
from mairadar.model import Chart, ChartBundle
from mairadar.io import write_bundle

metadata = Chart(title="Example", difficulty_index=5, chart_type="dx",
                 chart_end_time_s=result.chart_end_time_s,
                 last_event_end_s=result.last_event_end_s)
bundle = ChartBundle(metadata, result.events, result.diagnostics, result.complete)
write_bundle(bundle, "data/parsed")
```

曲绘是可选导出附件。write_bundle 的 cover_path 不传或为 None 时不附带曲绘；传入 JPG/JPEG/PNG/WebP 文件时，原样复制为 cover.jpg/cover.jpeg/cover.png/cover.webp，不解码、压缩或修改原图。manifest.assets.cover 保存相对文件名，file_sha256 包含曲绘的完整性校验。read_bundle 会校验附件；read_cover_path 返回校验后的曲绘路径。无 assets 字段的 events-0.3 bundle 仍可读取。

覆盖导出只包含本次指定的附件：API 在 overwrite=True 时不传 cover_path，会去掉旧包中登记的曲绘。附件与 CSV 一起先写入临时目录，复制或替换失败不会留下新旧文件混合的包。未在 manifest 登记的用户额外文件仍阻止覆盖。

模型校验独立位于 mairadar.validation：validate_result 用于纯文本结果，validate_bundle 用于附带 metadata 的结果；无需先导出文件。CSV write/read 会调用校验。

## 统一 CLI

```bash
python scripts/mairadar.py --mode full --input data/raw --output outputs/full
python scripts/mairadar.py --mode full --input path/to/maidata.txt --output outputs/full --difficulty 6
python scripts/mairadar.py --mode full --input Example.simai --output outputs/full --difficulty 5 --chart-type dx
python scripts/mairadar.py --mode parse_only --input data/raw --output data/parsed
```

full 模式在内存中依次完成解析、特征分析、映射和总表导出，不生成中间事件 bundle。
parse_only 模式只完成原始文件发现、解析与事件 bundle 写入，不构造 analyzer、mapper 或
报告 exporter；也可直接使用上文的 parse_chart、parse_file、write_bundle API。

--difficulty 指定一个或多个 inote 编号，--chart-type 显式设置 dx/sd；这两个参数用于 full
和 parse_only。两者递归读取 maidata.txt、majdata.txt 和 .simai，并按 bg.png、bg.jpg、
bg.jpeg、bg.webp 的顺序寻找同目录曲绘。parse_only 每张谱面写一个目录；不完整结果在
能确定导出名称与 DX/SD 时仍会保存为 partial bundle，并让进程返回非零。输出目录必须是
新目录或空目录。

其余模式为 analysis（已有 bundle → 特征分析，终端输出 JSON）和 analysis_score（已有 bundle → 特征分析、映射和导出）。参数和输出格式见 [分析说明](ANALYSIS.md)。

- 退出码 0：所有请求成功。
- 退出码 1：部分解析、分析、映射、读写失败，或输入输出条件无效。
- 退出码 2：命令行参数不合法。

物件错误保留源定位，时间错误停止该谱面后续扫描；不完整结果不会生成有效特征或分数。连接 Slide 按固定 MajdataPlay bar 数解析逐段时间，扩展语法限制见 SYNTAX_SUPPORT.md。EOF info 不导致 partial。所有解析时间不含音频 offset。

本地未安装包时，可用 PYTHONPATH=src 导入；脚本入口会自行设置源码路径。安装后命令是 mairadar；也支持 `PYTHONPATH=src python -m mairadar`。当前机器可用 /opt/anaconda3/bin/python3，系统 /usr/bin/python3 为 3.9。

## 测试与版本

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

当前格式是 events-0.3，旧 0.1/0.2 bundle 不自动迁移；重新导出即可。解析核心不清理历史目录。上游已做固定源码静态核对，尚未进行 .NET/Unity 运行时差分验证，不能把 round-trip 或 complete=true 当作独立正确性证明。
