"""Offline polynomial/ridge fitting; NumPy is an optional training dependency."""

from collections import Counter
from datetime import datetime, timezone
from itertools import combinations_with_replacement
import math
import random

from mairadar.constants import finite
from .runtime import FEATURES, SCHEMA_VERSION, PolynomialModel


def powers_for_degree(degree):
    if type(degree) is not int or not 1 <= degree <= 3:
        raise ValueError("Polynomial degree must be 1, 2 or 3")
    powers = []
    for size in range(1, degree + 1):
        for indexes in combinations_with_replacement(range(len(FEATURES)), size):
            powers.append([indexes.count(i) for i in range(len(FEATURES))])
    return powers


def raw_values(row):
    values = [finite(row.get(f"{name}_raw")) for name in FEATURES]
    if any(value is None for value in values):
        raise ValueError("missing_or_invalid_raw")
    return values


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Training requires NumPy: pip install 'simai-radar[regression]'") from exc
    return np


def fit_polynomial(x, y, degree=2, alpha=1.0):
    """Fit full polynomial interactions with an unpenalized intercept.

    Standardization is learned only from this training partition. Solve the
    augmented least-squares system by SVD rather than normal equations.
    """
    np = _numpy()
    powers = powers_for_degree(degree)
    if isinstance(alpha, bool) or not math.isfinite(alpha) or alpha < 0:
        raise ValueError("Ridge alpha must be finite and nonnegative")
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(FEATURES) or y.shape != (len(x),) or len(x) < 2:
        raise ValueError("Expected at least two seven-feature samples and matching targets")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Training values must be finite")
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    z = (x - center) / scale
    design = np.prod(z[:, None, :] ** np.asarray(powers)[None, :, :], axis=2)
    design = np.column_stack([np.ones(len(x)), design])
    if alpha:
        penalty = math.sqrt(alpha) * np.eye(design.shape[1])
        penalty[0, 0] = 0
        design = np.vstack([design, penalty])
        y = np.concatenate([y, np.zeros(len(penalty))])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    model = PolynomialModel({
        "schema_version": SCHEMA_VERSION, "input_kind": "raw", "features": list(FEATURES),
        "center": center.tolist(), "scale": scale.tolist(),
        "intercept": float(coefficients[0]),
        "terms": [{"powers": p, "coefficient": float(c)} for p, c in zip(powers, coefficients[1:])],
        "degree": degree, "alpha": alpha, "solver_rank": int(rank),
    })
    return model


def metrics(actual, predicted):
    errors = [p - y for y, p in zip(actual, predicted)]
    if not errors:
        raise ValueError("No evaluation samples")
    n = len(errors)
    squared = sum(e * e for e in errors)
    mean = sum(actual) / n
    variance = sum((y - mean) ** 2 for y in actual)
    absolute = sorted(abs(e) for e in errors)
    return {
        "count": n, "mae": sum(absolute) / n, "rmse": math.sqrt(squared / n),
        "r2": 1 - squared / variance if variance > 0 else None,
        "bias": sum(errors) / n, "p90_absolute_error": absolute[math.ceil(0.9 * n) - 1],
    }


def train(rows, *, degrees=(1, 2, 3), alphas=(0.1, 1.0, 10.0), seed=42, folds=5):
    """Tune on grouped CV, evaluate untouched title holdout, refit on all labels."""
    if not degrees or not alphas or type(folds) is not int or folds < 2:
        raise ValueError("Provide candidate degrees/alphas and at least two folds")
    for degree in degrees:
        powers_for_degree(degree)
    for alpha in alphas:
        if isinstance(alpha, bool) or not math.isfinite(alpha) or alpha < 0:
            raise ValueError("Ridge alpha must be finite and nonnegative")
    eligible, exclusions, row_reasons = [], Counter(), {}
    for index, row in enumerate(rows):
        reason = None
        target = finite(row.get("official_constant"))
        if row.get("status", "ok") != "ok":
            reason = "input_not_ok"
        elif row.get("match_status") != "matched" or target is None or not 0 < target <= 20:
            reason = "no_matched_constant"
        elif not row.get("title"):
            reason = "missing_title"
        else:
            try:
                x = raw_values(row)
            except ValueError:
                reason = "missing_or_invalid_raw"
        if reason:
            exclusions[reason] += 1
            row_reasons[index] = reason
        else:
            eligible.append((index, row.get("matched_title") or row["title"], x, target))
    titles = sorted({item[1] for item in eligible})
    if len(titles) < 6:
        raise ValueError("Need at least six matched song titles with complete raw features for evaluation")
    random.Random(seed).shuffle(titles)
    holdout_titles = set(titles[:max(1, math.ceil(len(titles) * 0.2))])
    development_titles = titles[len(holdout_titles):]
    fold_titles = [set(development_titles[i::min(folds, len(development_titles))])
                   for i in range(min(folds, len(development_titles)))]
    development = [item for item in eligible if item[1] not in holdout_titles]
    holdout = [item for item in eligible if item[1] in holdout_titles]

    def fit(items, degree, alpha):
        return fit_polynomial([i[2] for i in items], [i[3] for i in items], degree, alpha)

    def evaluate(model, items):
        return [model.predict(i[2]) for i in items]

    candidates = []
    for degree in degrees:
        for alpha in alphas:
            actual, predicted = [], []
            for validation_titles in fold_titles:
                training = [i for i in development if i[1] not in validation_titles]
                validation = [i for i in development if i[1] in validation_titles]
                model = fit(training, degree, alpha)
                actual.extend(i[3] for i in validation)
                predicted.extend(evaluate(model, validation))
            candidates.append({"degree": degree, "alpha": alpha, **metrics(actual, predicted)})
    best = min(candidates, key=lambda item: (item["rmse"], item["degree"], -item["alpha"]))
    evaluation_model = fit(development, best["degree"], best["alpha"])
    holdout_predictions = evaluate(evaluation_model, holdout)
    final_model = fit(eligible, best["degree"], best["alpha"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "input_rows": len(rows), "training_rows": len(eligible), "excluded": dict(exclusions),
        "selection": "minimum grouped cross-validation RMSE; holdout untouched during selection",
        "group_by": "matched source title, falling back to input title (all difficulties and DX/SD together)",
        "holdout_fraction": 0.2, "folds": len(fold_titles),
        "development_titles": sorted(development_titles),
        "holdout_titles": sorted(holdout_titles),
        "candidates": candidates, "selected": {"degree": best["degree"], "alpha": best["alpha"]},
        "holdout": metrics([i[3] for i in holdout], holdout_predictions),
        "holdout_mean_baseline": metrics(
            [i[3] for i in holdout], [sum(i[3] for i in development) / len(development)] * len(holdout),
        ),
        "refit_training": metrics([i[3] for i in eligible], evaluate(final_model, eligible)),
        "note": "Holdout metrics belong to evaluation_model.json. model.json is refit on all eligible labels.",
        "sources": sorted({row.get("constant_source", "") for row in rows if row.get("constant_source")}),
        "label_snapshot_times": sorted({row.get("constant_fetched_at", "") for row in rows if row.get("constant_fetched_at")}),
    }
    data = final_model.to_dict()
    data["training"] = {
        "created_at": report["generated_at"], "rows": len(eligible),
        "raw_min": [min(i[2][j] for i in eligible) for j in range(len(FEATURES))],
        "raw_max": [max(i[2][j] for i in eligible) for j in range(len(FEATURES))],
        "target_min": min(i[3] for i in eligible), "target_max": max(i[3] for i in eligible),
        "label_sources": report["sources"],
        "label_snapshot_times": report["label_snapshot_times"],
    }
    final_model = PolynomialModel(data)
    held_out = {i[0]: p for i, p in zip(holdout, holdout_predictions)}
    predictions = []
    vectors = []
    for index, row in enumerate(rows):
        result = {**row, "training_exclusion": row_reasons.get(index, ""),
                  "evaluation_split": "excluded" if index in row_reasons else (
                      "holdout" if (row.get("matched_title") or row["title"]) in holdout_titles else "development"),
                  "holdout_prediction": held_out.get(index)}
        try:
            if row.get("status", "ok") != "ok":
                raise ValueError("input_not_ok")
            raw = raw_values(row)
            result.update(fitted_constant=final_model.predict(raw), prediction_status="ok")
            if len(vectors) < 8:
                vectors.append({"raw": raw, "expected": result["fitted_constant"]})
        except ValueError as exc:
            result.update(fitted_constant=None, prediction_status=str(exc))
        predictions.append(result)
    return final_model, evaluation_model, report, predictions, vectors
