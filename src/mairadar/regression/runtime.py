"""Portable polynomial evaluator: standard library only, no parser/UI/network.

This file can also be copied and imported on its own by a host application.
"""

from collections.abc import Mapping
from copy import deepcopy
import json
import math
from pathlib import Path

FEATURES = (
    "note", "peak", "sweep", "slide_tricky", "slide_sequence", "jack", "slide_cumulate",
)
SCHEMA_VERSION = "mairadar-polynomial-1"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Model and inputs must contain finite numbers")
    return float(value)


class PolynomialModel:
    def __init__(self, data):
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Expected {SCHEMA_VERSION}")
        if data.get("input_kind") != "raw" or data.get("features") != list(FEATURES):
            raise ValueError("Model must use the seven raw features in the documented order")
        self.features = FEATURES
        self.center = tuple(_number(v) for v in data.get("center", []))
        self.scale = tuple(_number(v) for v in data.get("scale", []))
        if len(self.center) != len(FEATURES) or len(self.scale) != len(FEATURES) or min(self.scale) <= 0:
            raise ValueError("Model center/scale must have seven values and positive scales")
        self.intercept = _number(data.get("intercept"))
        terms = data.get("terms")
        if not isinstance(terms, list) or not terms:
            raise ValueError("Model requires polynomial terms")
        self.terms = []
        seen = set()
        for term in terms:
            if not isinstance(term, dict):
                raise ValueError("Invalid polynomial term")
            powers = tuple(term.get("powers", []))
            if len(powers) != len(FEATURES) or any(type(p) is not int or p < 0 for p in powers):
                raise ValueError("Powers must be seven nonnegative integers")
            if not 1 <= sum(powers) <= 3 or powers in seen:
                raise ValueError("Terms must be unique and have degree 1 through 3")
            seen.add(powers)
            self.terms.append((powers, _number(term.get("coefficient"))))
        self._data = deepcopy(data)

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def to_dict(self):
        return deepcopy(self._data)

    def predict(self, raw):
        if isinstance(raw, Mapping):
            if any(name not in raw for name in FEATURES):
                raise ValueError("Prediction requires all seven raw features")
            values = [raw[name] for name in FEATURES]
        else:
            values = list(raw)
        if len(values) != len(FEATURES):
            raise ValueError("Prediction requires seven raw features in model order")
        z = [(_number(v) - c) / s for v, c, s in zip(values, self.center, self.scale)]
        value = self.intercept
        try:
            for powers, coefficient in self.terms:
                term = coefficient
                for x, power in zip(z, powers):
                    term *= x ** power
                value += term
        except OverflowError as exc:
            raise ValueError("Prediction overflow") from exc
        if not math.isfinite(value):
            raise ValueError("Prediction overflow")
        return value
