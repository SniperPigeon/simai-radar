"""Dispatch independent feature classes; never parse text or access files."""

from collections.abc import Mapping
from copy import deepcopy
import math
import re

from mairadar.model import ParseResult
from mairadar.validation import validate_result
from .config import FEATURES
from .model import AnalysisContext, AnalysisIssue, AnalysisResult, FeatureAnalyzer, FeatureResult


class ChartAnalyzer:
    def __init__(self, features: Mapping[str, type[FeatureAnalyzer]] | None = None):
        #注册所有的分析器
        self._features = dict(FEATURES if features is None else features)
        if not self._features:
            raise ValueError("Configure at least one feature")
        for name, analyzer in self._features.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError(f"Invalid feature name: {name!r}")
            if not isinstance(analyzer, type) or not callable(getattr(analyzer, "analyze", None)):
                raise ValueError(f"Feature {name} must reference an analyzer class")

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(self._features)

    def analyze(self, parsed: ParseResult) -> AnalysisResult:
        issue = None
        try:
            validate_result(parsed)
        except (ValueError, TypeError, AttributeError) as exc:
            issue = AnalysisIssue("INVALID_INPUT", str(exc))
        if issue is None and not parsed.complete:
            issue = AnalysisIssue("PARSE_INCOMPLETE", "Analysis requires a complete parse result")
        if issue is not None:
            return AnalysisResult(
                {name: FeatureResult(None, success=False) for name in self._features},
                tuple(deepcopy(parsed.diagnostics)), (issue,),
            )
        # 逐个分析
        context = AnalysisContext(tuple(parsed.events), parsed.chart_end_time_s, parsed.last_event_end_s)
        results = {}
        issues = []
        for name, analyzer in self._features.items():
            try:
                # Fresh instances and snapshots isolate charts and independently configured features.
                result = analyzer().analyze(deepcopy(context))
                self._validate_feature(result)
                results[name] = result
            except Exception as exc:
                results[name] = FeatureResult(None, success=False)
                issues.append(AnalysisIssue("FEATURE_FAILED", str(exc), name))
        return AnalysisResult(results, tuple(deepcopy(parsed.diagnostics)), tuple(issues))

    @staticmethod
    def _validate_feature(result: FeatureResult) -> None:
        if not isinstance(result, FeatureResult):
            raise ValueError("Analyzer must return FeatureResult")
        if not isinstance(result.success, bool):
            raise ValueError("Feature success must be a boolean")
        if result.success:
            if (isinstance(result.data, bool) or not isinstance(result.data, (float, int))
                    or not math.isfinite(result.data)):
                raise ValueError("Successful feature must have a finite numeric value")
        elif result.data is not None:
            raise ValueError("Unsuccessful feature must have a null value")
