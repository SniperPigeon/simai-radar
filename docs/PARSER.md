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

曲绘是可选导出附件。write_bundle 的 cover_path 不传或为 None 时不附带曲绘；传入 JPG/JPEG/PNG/WebP 文件时，原样复制为 cover.jpg/cover.jpeg/cover.png/cover.webp，不解码、压缩或修改原图。manifest.assets.cover 保存相对文件名，file_sha256 包含曲绘的完整性校验。read_bundle 会校验附件；read_cover_path 返回校验后的曲绘路径。无 assets 字段的旧 events-0.2 bundle 仍可读取。

覆盖导出只包含本次指定的附件：API 在 overwrite=True 时不传 cover_path，会去掉旧包中登记的曲绘；CLI 默认重新发现源目录曲绘，--no-cover 则明确不附带。附件与 CSV 一起先写入临时目录，复制或替换失败不会留下新旧文件混合的包。未在 manifest 登记的用户额外文件仍阻止覆盖。

模型校验独立位于 mairadar.validation：validate_result 用于纯文本结果，validate_bundle 用于附带 metadata 的结果；无需先导出文件。CSV write/read 会调用校验。

## CLI

```bash
python scripts/parse_chart.py --input data/raw --output data/parsed
python scripts/parse_chart.py --input path/to/maidata.txt --output data/parsed --difficulty 6
python scripts/parse_chart.py --input Example.simai --output data/parsed --difficulty 5 --chart-type dx
```

| 参数 | 作用 |
| --- | --- |
| --input / -i | 必填，单个文本文件或目录 |
| --output / -o | 必填，结果根目录 |
| --difficulty / -d | 可选，一个或多个 inote 编号，默认全部存在的难度 |
| --chart-type | 可选，dx/sd，明确设置导出类型；未指定则读取 cabinet，兼容源 cabinate 别名 |
| --overwrite | 可选，替换既有的本格式导出目录 |
| --cover | 可选，显式曲绘路径，仅用于单文件输入 |
| --no-cover | 可选，不附带曲绘；与 --cover 互斥 |

输出目录为 `<title>-<difficulty_index>-dx/sd`，例如 `Link-6-sd`。不生成 hash 名、不自动附加序号、不维护目录到歌曲的注册表或去重库。缺少难度或类型的纯正文仍能正常解析，但导出需调用方提供 metadata。不得从歌曲名或物件种类猜 DX/SD。

目录名中的文件系统非法字符替换为下划线，原 title 保留在 charts.csv。普通已存在目录需要 --overwrite；该选项只是文件写入策略，不比较输入内容或判断是不是同一歌曲。仅允许替换本格式且不含额外文件的导出目录；使用临时目录写齐后替换，失败回滚。

CLI 递归读取 maidata.txt、majdata.txt 和 .simai；默认按 bg.png、bg.jpg、bg.jpeg、bg.webp 的顺序寻找每个谱面文件同目录的曲绘并附带，找不到时正常导出无曲绘包。--cover 可覆盖单文件的选择；目录模式分别发现各自的曲绘，不能用一个 --cover 误配整批。音频、视频和 ZIP 不打包。批量输出重复命名如何处理由调用方的输入与覆盖选项决定，库不负责曲库管理。

- 退出码 0：所有请求完整解析并导出。
- 退出码 1：部分解析或读写/导出 metadata 错误。
- 退出码 2：参数错误、输入不存在或没有匹配文件。

物件错误会记录源定位，时间错误停止该谱面后续扫描；可确定结果的 manifest.complete=false。连接 Slide 逐段几何时间、K 和播放器速度/拍号扩展的限制见 SYNTAX_SUPPORT.md。EOF info 不导致 partial。所有解析时间不含音频 offset。

本地未安装包时，可用 `PYTHONPATH=src` 导入；脚本入口会自行设置 src 路径。安装后命令是 mairadar-parse；也支持 `PYTHONPATH=src python -m mairadar.parser`。当前机器可用 /opt/anaconda3/bin/python3，系统 /usr/bin/python3 为 3.9。

## 测试与版本

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

当前格式是 events-0.2，旧 0.1 表头不自动迁移；重新导出即可。解析核心不清理历史目录。上游已做固定源码静态核对，尚未进行 .NET/Unity 运行时差分验证，不能把 round-trip 或 complete=true 当作独立正确性证明。
