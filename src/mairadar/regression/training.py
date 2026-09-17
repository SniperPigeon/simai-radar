"""Offline sklearn training and export to the standard-library polynomial runtime."""

from collections import Counter
from datetime import datetime, timezone
import math

import numpy as np
import sklearn
from sklearn.base import clone
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from mairadar.constants import finite
from .runtime import SCHEMA_VERSION, PolynomialModel


# Edit these raw field names to choose training inputs, in model input order.
# Stats use flattened names such as note_density_mean or sweep_motion_density.
TRAINING_FEATURES = (
    "note", "peak", "sweep", "slide_tricky", "slide_sequence", "jack", "slide_cumulate",
)


def export_model(pipeline, feature_names):
    """Export fitted sklearn parameters; inference never imports sklearn."""
    scaler, polynomial, ridge = pipeline.steps[0][1], pipeline.steps[1][1], pipeline.steps[2][1]
    return PolynomialModel({
        "schema_version": SCHEMA_VERSION, "input_kind": "raw", "features": list(feature_names),
        "center": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "intercept": float(ridge.intercept_), "degree": polynomial.degree, "alpha": ridge.alpha,
        "terms": [{"powers": powers, "coefficient": coefficient}
                  for powers, coefficient in zip(polynomial.powers_.tolist(), ridge.coef_.tolist())],
    })


def metrics(actual, predicted):
    errors = np.asarray(predicted) - actual
    return {
        "count": len(actual), "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(root_mean_squared_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)) if len(actual) > 1 else None,
        "bias": float(errors.mean()), "p90_absolute_error": float(np.quantile(abs(errors), 0.9)),
    }


def train(rows, *, features=None, degrees=(1, 2, 3, 4), alphas=(0.1, 1.0, 10.0), seed=42, folds=5):
    """Fit on level-stratified rows, evaluate holdout, then refit for deployment."""
    if not degrees or any(type(d) is not int or not 1 <= d <= 4 for d in degrees):
        raise ValueError("Provide polynomial degrees from 1 through 4")
    features = tuple(TRAINING_FEATURES if features is None else features)
    if (not features or any(not isinstance(name, str) or not name for name in features)
            or len(set(features)) != len(features) or "fitted_constant" in features):
        raise ValueError("Choose unique raw inputs; fitted_constant cannot be its own training input")
    eligible, raw, targets, levels, reasons = [], [], [], [], {}
    for index, row in enumerate(rows):
        target = finite(row.get("official_constant"))
        values = [finite(row.get(f"{name}_raw")) for name in features]
        if row.get("status", "ok") != "ok":
            reasons[index] = "input_not_ok"
        elif row.get("match_status") != "matched" or target is None or not 0 < target <= 20:
            reasons[index] = "no_matched_constant"
        elif None in values:
            reasons[index] = "missing_or_invalid_raw"
        else:
            eligible.append(index)
            raw.append(values)
            targets.append(target)
            levels.append(str(row.get("level") or row.get("level_text") or math.floor(target)).strip())
    if not eligible:
        raise ValueError("No matched charts with complete raw features")
    x, y, levels = np.asarray(raw), np.asarray(targets), np.asarray(levels)
    counts = Counter(levels)
    # sklearn's holdout splitter requires two samples per stratum. Keep the
    # genuinely unstratifiable singleton rows in development; all splitting is sklearn's.
    regular = np.array([i for i, level in enumerate(levels) if counts[level] > 1], dtype=int)
    singletons = np.array([i for i, level in enumerate(levels) if counts[level] == 1], dtype=int)
    development, holdout = train_test_split(regular, test_size=0.2, stratify=levels[regular], random_state=seed)
    development = np.concatenate([development, singletons])
    cv = list(StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(
        x[development], levels[development],
    ))
    pipeline = Pipeline([
        ("scale", StandardScaler()), ("poly", PolynomialFeatures(include_bias=False)),
        ("ridge", Ridge(solver="svd")),
    ])
    search = GridSearchCV(
        pipeline, {"poly__degree": list(degrees), "ridge__alpha": list(alphas)},
        scoring={"rmse": "neg_root_mean_squared_error", "mae": "neg_mean_absolute_error"},
        refit="rmse", cv=cv, error_score="raise",
    ).fit(x[development], y[development])
    evaluation = search.best_estimator_
    deployed = clone(evaluation).fit(x, y)
    model, evaluation_model = export_model(deployed, features), export_model(evaluation, features)
    candidates = [
        {"degree": params["poly__degree"], "alpha": params["ridge__alpha"],
         "rmse": float(-rmse), "mae": float(-mae)}
        for params, rmse, mae in zip(search.cv_results_["params"],
                                     search.cv_results_["mean_test_rmse"], search.cv_results_["mean_test_mae"])
    ]
    holdout_predictions = evaluation.predict(x[holdout])
    row_numbers = np.asarray(eligible) + 1
    development_counts, holdout_counts = Counter(levels[development]), Counter(levels[holdout])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "trainer": f"scikit-learn {sklearn.__version__}",
        "seed": seed, "features": list(features), "input_rows": len(rows), "training_rows": len(eligible), "excluded": dict(Counter(reasons.values())),
        "selection": "minimum mean CV RMSE; holdout untouched during selection",
        "split_method": "level_stratified_rows", "holdout_fraction": 0.2,
        "actual_holdout_fraction": len(holdout) / len(eligible), "folds": len(cv),
        "development_row_numbers": sorted(row_numbers[development].tolist()),
        "holdout_row_numbers": sorted(row_numbers[holdout].tolist()),
        "cv_validation_row_numbers": [sorted(row_numbers[development[test]].tolist()) for _, test in cv],
        "level_counts": {level: {"total": count, "development": development_counts[level],
                                  "holdout": holdout_counts[level]} for level, count in sorted(counts.items())},
        "singleton_levels": sorted(level for level, count in counts.items() if count == 1),
        "candidates": candidates,
        "selected": {"degree": search.best_params_["poly__degree"], "alpha": search.best_params_["ridge__alpha"]},
        "holdout": metrics(y[holdout], holdout_predictions),
        "holdout_mean_baseline": metrics(y[holdout], np.full(len(holdout), y[development].mean())),
        "refit_training": metrics(y, deployed.predict(x)),
        "note": "Holdout metrics belong to evaluation_model.json. model.json is refit on all eligible labels.",
        "sources": sorted({r["constant_source"] for r in rows if r.get("constant_source")}),
        "label_snapshot_times": sorted({r["constant_fetched_at"] for r in rows if r.get("constant_fetched_at")}),
    }
    data = model.to_dict()
    data["training"] = {
        "created_at": report["generated_at"], "rows": len(eligible), "trainer": report["trainer"],
        "raw_min": x.min(axis=0).tolist(), "raw_max": x.max(axis=0).tolist(),
        "target_min": float(y.min()), "target_max": float(y.max()),
        "label_sources": report["sources"], "label_snapshot_times": report["label_snapshot_times"],
    }
    model = PolynomialModel(data)
    held_out = dict(zip((eligible[i] for i in holdout), holdout_predictions.tolist()))
    level_by_index = dict(zip(eligible, levels.tolist()))
    fold_by_index = {eligible[development[i]]: fold + 1 for fold, (_, test) in enumerate(cv) for i in test}
    predictions, vectors = [], []
    for index, row in enumerate(rows):
        result = {**row, "training_exclusion": reasons.get(index, ""),
                  "evaluation_split": "excluded" if index in reasons else ("holdout" if index in held_out else "development"),
                  "evaluation_level": level_by_index.get(index), "cv_fold": fold_by_index.get(index),
                  "holdout_prediction": held_out.get(index)}
        try:
            if row.get("status", "ok") != "ok":
                raise ValueError("input_not_ok")
            values = [finite(row.get(f"{name}_raw")) for name in features]
            result.update(fitted_constant=model.predict(values), prediction_status="ok")
            if len(vectors) < 8:
                vectors.append({"raw": values, "expected": result["fitted_constant"]})
        except ValueError as exc:
            result.update(fitted_constant=None, prediction_status=str(exc))
        predictions.append(result)
    return model, evaluation_model, report, predictions, vectors
