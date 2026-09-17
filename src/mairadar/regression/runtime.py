"""Portable polynomial evaluator: standard library only, no parser/UI/network.

This file can also be copied and imported on its own by a host application.
"""

from collections.abc import Mapping
from copy import deepcopy
import json
import math
from pathlib import Path

SCHEMA_VERSION = "mairadar-polynomial-2"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Model and inputs must contain finite numbers")
    return float(value)


class PolynomialModel:
    def __init__(self, data):
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Expected {SCHEMA_VERSION}")
        names = data.get("features")
        if (data.get("input_kind") != "raw" or not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)):
            raise ValueError("Model must declare unique raw feature names in input order")
        self.features = tuple(names)
        self.center = tuple(_number(v) for v in data.get("center", []))
        self.scale = tuple(_number(v) for v in data.get("scale", []))
        if len(self.center) != len(names) or len(self.scale) != len(names) or min(self.scale) <= 0:
            raise ValueError("Model center/scale must match its features and have positive scales")
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
            if len(powers) != len(names) or any(type(p) is not int or p < 0 for p in powers):
                raise ValueError("Powers must be nonnegative integers matching the features")
            if not 1 <= sum(powers) <= 4 or powers in seen:
                raise ValueError("Terms must be unique and have degree 1 through 4")
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
            if any(name not in raw for name in self.features):
                raise ValueError("Prediction requires every model input feature")
            values = [raw[name] for name in self.features]
        else:
            values = list(raw)
        if len(values) != len(self.features):
            raise ValueError("Prediction inputs must match model feature order and count")
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
