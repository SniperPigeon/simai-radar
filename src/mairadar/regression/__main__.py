"""Offline constant regression CLI. Inference uses only the standard library."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from mairadar.constants import finite, read_rows, write_json, write_rows
from .runtime import FEATURES, PolynomialModel


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("fit", help="fit a joined constants.csv; export portable parameters")
    fit.add_argument("--input", type=Path, required=True)
    fit.add_argument("--output", type=Path, required=True, help="new output directory")
    fit.add_argument("--degrees", type=int, nargs="+", default=[1, 2, 3, 4])
    fit.add_argument("--alphas", type=float, nargs="+", default=[0.1, 1.0, 10.0])
    fit.add_argument("--seed", type=int, default=42)
    fit.add_argument("--folds", type=int, default=5)
    predict = commands.add_parser("predict", help="apply frozen parameters without NumPy")
    predict.add_argument("--input", type=Path, required=True)
    predict.add_argument("--model", type=Path, required=True)
    predict.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(f"Output already exists: {args.output}")
        rows = read_rows(args.input)
        if args.command == "predict":
            model = PolynomialModel.load(args.model)
            failures = 0
            for row in rows:
                try:
                    if row.get("status", "ok") != "ok":
                        raise ValueError("input_not_ok")
                    values = [finite(row.get(f"{name}_raw")) for name in FEATURES]
                    row.update(fitted_constant=model.predict(values), prediction_status="ok")
                except ValueError as exc:
                    failures += 1
                    row.update(fitted_constant=None, prediction_status=str(exc))
            write_rows(args.output, rows)
            print(json.dumps({"charts": len(rows), "failed": failures, "output": str(args.output)}))
            return int(failures > 0)
        try:
            from .training import train
        except ImportError as exc:
            raise RuntimeError("Training requires scikit-learn: pip install 'simai-radar[regression]'") from exc
        model, evaluation, report, predictions, vectors = train(
            rows, degrees=args.degrees, alphas=args.alphas, seed=args.seed, folds=args.folds,
        )
        report["input"] = str(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{args.output.name}-", dir=args.output.parent))
        try:
            write_json(staging / "model.json", model.to_dict())
            write_json(staging / "evaluation_model.json", evaluation.to_dict())
            write_json(staging / "report.json", report)
            write_json(staging / "test_vectors.json", {"features": list(FEATURES), "tolerance": 1e-9, "vectors": vectors})
            write_rows(staging / "predictions.csv", predictions)
            staging.rename(args.output)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        print(json.dumps({"output": str(args.output), "training_rows": report["training_rows"],
                          "excluded": report["excluded"], "selected": report["selected"],
                          "holdout": report["holdout"]}, ensure_ascii=False))
        return int(any(key != "no_matched_constant" for key in report["excluded"]))
    except Exception as exc:
        print(f"Constant regression failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
