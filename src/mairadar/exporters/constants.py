"""Optional presentation adapter for frozen constants and raw-feature models."""

from mairadar.constants import ConstantTable
from mairadar.regression import PolynomialModel
from mairadar.analysis import AnalysisResult, FeatureResult
from mairadar.scoring import FeatureScoreTransformer, IdentityMapper, load_mapping_profile


class ConstantAnnotations:
    def __init__(self, table=None, model=None, mapping_profile=None):
        self.table = ConstantTable.load(table) if table is not None else None
        self.model = PolynomialModel.load(model) if model is not None else None
        if self.model is not None and "fitted_constant" in self.model.features:
            raise ValueError("A constant model cannot depend on its own fitted_constant")
        self.mapper = load_mapping_profile(mapping_profile) if mapping_profile is not None else (
            FeatureScoreTransformer({"fitted_constant": IdentityMapper()}, mapping_version="constant-identity")
            if self.model is not None else None
        )

    def chart(self, title, difficulty, kind, raw, status="ok", artist=None):
        fields = {}
        if self.table is not None:
            fields.update(self.table.lookup(title, difficulty, kind, artist))
        if self.model is not None:
            try:
                if status != "ok":
                    raise ValueError("input_not_ok")
                fields.update(fittedConstant=self.model.predict(raw), constantPredictionStatus="ok")
            except ValueError as exc:
                fields.update(fittedConstant=None, constantPredictionStatus=str(exc))
        return fields

    def payload(self, payload):
        failures = prediction_failures = mapping_failures = 0
        for song in payload["songs"]:
            for chart in song["charts"]:
                raw = chart["rawFeatures"]
                mapped = chart["mappedFeatures"]
                chart.update(self.chart(
                    song["title"], chart["difficulty"], chart["kind"], raw,
                    chart.get("status", "ok"), song.get("artist"),
                ))
                prediction_failed = chart.get("constantPredictionStatus", "ok") != "ok"
                prediction_failures += prediction_failed
                mapping_failed = False
                if self.model is not None:
                    raw["fitted_constant"] = chart["fittedConstant"]
                if self.mapper is not None:
                    result = self.mapper.transform(AnalysisResult({
                        name: FeatureResult(value, value is not None) for name, value in raw.items()
                    }))
                    mapping_failed = any(item.status != "ok" for item in result.features.values())
                    mapping_failures += mapping_failed
                    mapped.update({name: item.value for name, item in result.features.items()})
                    versions = payload.setdefault("mappingVersions", [])
                    if result.mapping_version not in versions:
                        versions.append(result.mapping_version)
                chart["scores"] = {name: mapped.get(name) for name in chart["scores"]}
                chart["rawScores"] = {name: raw.get(name) for name in chart["rawScores"]}
                failures += prediction_failed or mapping_failed
        if self.model is not None:
            payload["constantModel"] = self.model.to_dict()
            payload.setdefault("stats", {})["constantPredictionFailedCount"] = prediction_failures
        if self.mapper is not None:
            payload.setdefault("stats", {})["scoreMappingFailedCount"] = mapping_failures
        return failures
