"""Optional fitted-constant feature, evaluated on raw analysis before scoring."""

from dataclasses import replace

from mairadar.analysis.model import AnalysisIssue, FeatureResult


def with_prediction(result, model):
    if "fitted_constant" in model.features:
        raise ValueError("A constant model cannot depend on its own fitted_constant")
    features = dict(result.features)
    diagnostics = result.diagnostics
    if result.is_cancelled:
        features["fitted_constant"] = FeatureResult(None, success=False)
        return replace(result, features=features)
    try:
        raw = {name: item.data if item.success else None for name, item in result.features.items()}
        features["fitted_constant"] = FeatureResult(model.predict(raw))
    except ValueError as exc:
        features["fitted_constant"] = FeatureResult(None, success=False)
        diagnostics += (AnalysisIssue("PREDICTION_FAILED", str(exc), "fitted_constant"),)
    return replace(result, features=features, diagnostics=diagnostics)
