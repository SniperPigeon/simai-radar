"""Optional presentation adapter for frozen constants and raw-feature models."""

from mairadar.constants import ConstantTable
from mairadar.regression import PolynomialModel


class ConstantAnnotations:
    def __init__(self, table=None, model=None):
        self.table = ConstantTable.load(table) if table is not None else None
        self.model = PolynomialModel.load(model) if model is not None else None

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
        failures = 0
        for song in payload["songs"]:
            for chart in song["charts"]:
                for key in ("constantRegion", "constantSourceKind", "constantDeletedDate"):
                    chart.pop(key, None)
                chart.update(self.chart(
                    song["title"], chart["difficulty"], chart["kind"], chart["rawScores"],
                    chart.get("status", "ok"), song.get("artist"),
                ))
                failures += chart.get("constantPredictionStatus", "ok") != "ok"
        if self.model is not None:
            payload["constantModel"] = self.model.to_dict()
            payload.setdefault("stats", {})["constantPredictionFailedCount"] = failures
        return failures
