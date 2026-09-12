# simai-radar

Python Simai 文本解析库。返回统一事件时间轴，供播放器集成和独立分析使用；不负责歌曲管理、跨谱面身份或去重。

```python
from mairadar.parser import parse_chart

result = parse_chart("(120){4}1,1?-5[4:1],E")
# result.events / result.diagnostics / result.complete
```

Python 3.11+，仅标准库。核心接收单张谱面的 inote 正文，返回一个 ParseResult，不需要 metadata 或输入/输出路径。完整 maidata 文本可用 parse_text 解析为多个附带 metadata 的 ChartBundle。

文件处理与导出是外围适配：

```bash
python scripts/parse_chart.py --input data/raw --output data/parsed
python scripts/parse_chart.py --input path/to/maidata.txt --output data/parsed --difficulty 6 --overwrite
```

输出目录为 `<title>-<difficulty_index>-dx/sd`，例如 `Link-6-sd`，包含 events.csv、charts.csv、diagnostics.csv、manifest.json。DX/SD 来自 cabinet/cabinate 元数据；缺失时可用 --chart-type 指定。文件目录名不使用 hash；events 不含 chart_id，event_id 和 head_event_id 为单次结果内的整数编号。

```text
src/mairadar/
  model.py       事件、解析结果和可选 metadata
  parser/        纯文本时间与物件语义
  validation.py  纯模型校验
  io.py          文件读取与 CSV 导出/读取
  cli.py         可选的参数与批量文件处理
scripts/
  parse_chart.py 本地 CLI 入口
  map_scores.py  后续评分映射占位
res/             小型人工 golden、配置和固定引用
tests/           合成语义及外围接口测试
data/            外部输入与分析产物
outputs/         预览和验证报告
```

BPM 是 timing 事件；Slide 形状内联，有头/无头均保留声明时间；连接 Slide 段数组不复制成多个路径事件。未知语法明确诊断，不猜节奏。连接段的几何时间与部分播放器扩展仍未实现；任何 partial 或读写失败，CLI 均返回非零。实际边界见 [语法支持](docs/SYNTAX_SUPPORT.md)。

[调用说明](docs/PARSER.md) · [events-0.2 格式](docs/SCHEMA_PROPOSAL.md) · [架构](docs/ARCHITECTURE.md)

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

未安装包时可使用 PYTHONPATH=src；CLI 脚本无需安装。当前机器可用 /opt/anaconda3/bin/python3，系统 Python 3.9 不满足要求。

固定上游引用见 [upstreams.json](res/reference/upstreams.json)。已做源码静态核对、合成测试和真实输入抽查，尚未运行 .NET/Unity 差分验证。真实谱面、音频和生成产物不纳入提交；HANDOFF.md 保留为一次性历史快照。
