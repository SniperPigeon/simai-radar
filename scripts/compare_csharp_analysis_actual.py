#!/usr/bin/env python3
"""Migration-only real-chart differential for the six ported C# dimensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mairadar.analysis import ChartAnalyzer
from mairadar.parser import parse_chart


FIELD = re.compile(r"(?m)^[ \t]*&([A-Za-z][A-Za-z0-9_]*)=")
FEATURES = (
    "note", "peak", "slide_tricky", "slide_sequence", "jack", "slide_cumulate",
)
ABSOLUTE_TOLERANCE = 1e-8
RELATIVE_TOLERANCE = 1e-8


def maidata_field(text: str, key: str) -> str | None:
    matches = list(FIELD.finditer(text.removeprefix("\ufeff")))
    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        values[match[1]] = text[match.end():end].strip()
    return values.get(key)


def run_csharp(probe: Path, body: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as stream:
        stream.write(body)
        stream.flush()
        completed = subprocess.run(
            ["dotnet", str(probe), stream.name],
            check=False, capture_output=True, text=True,
        )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"probe exited {completed.returncode}")
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument(
        "--probe-dll", type=Path,
        default=Path("integrations/csharp-runtime-probe/bin/Debug/net10.0/CsharpRuntimeProbe.dll"),
    )
    args = parser.parse_args()
    maxima = {name: (0.0, 0.0, "") for name in FEATURES}
    failures = []
    for path in args.paths:
        body = maidata_field(path.read_text(encoding="utf-8-sig"), f"inote_{args.difficulty}")
        if body is None:
            failures.append((str(path), f"missing inote_{args.difficulty}"))
            continue
        parsed = parse_chart(body)
        if not parsed.complete:
            failures.append((str(path), "Python parse incomplete"))
            continue
        python = ChartAnalyzer().analyze(parsed)
        try:
            csharp = run_csharp(args.probe_dll, body)
        except (RuntimeError, json.JSONDecodeError) as exception:
            failures.append((str(path), str(exception)))
            continue
        for name in FEATURES:
            left = python.features[name]
            right = csharp.get("features", {}).get(name)
            if not left.success or not right or not right["IsSuccess"]:
                failures.append((str(path), f"{name}: Python={left.success}, C#={right}"))
                continue
            expected, actual = float(left.data), float(right["Value"])
            absolute = abs(actual - expected)
            relative = absolute / max(abs(expected), 1e-12)
            current = maxima[name]
            if absolute > current[0]:
                maxima[name] = (absolute, relative, str(path))
            if absolute > ABSOLUTE_TOLERANCE and relative > RELATIVE_TOLERANCE:
                failures.append((
                    str(path),
                    f"{name}: C#={actual:.15g} Python={expected:.15g} "
                    f"abs={absolute:.6g} rel={relative:.6g}",
                ))
        print(f"PASS {path}")
    print("max errors:")
    for name in FEATURES:
        absolute, relative, path = maxima[name]
        print(f"  {name}: abs={absolute:.12g} rel={relative:.12g} {path}")
    for path, message in failures:
        print(f"FAIL {path}: {message}")
    print(f"summary: {len(args.paths) - len({path for path, _ in failures})} charts without failures, "
          f"{len({path for path, _ in failures})} failed")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
