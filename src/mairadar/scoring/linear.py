"""Dummy piecewise-linear mapping; thresholds are supplied raw values, not fitted."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DummyPnMapper:
    p50: float
    p100: float

    def __post_init__(self) -> None:
        for value in (self.p50, self.p100):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("P50 and P100 must be finite numbers")
        if not 0 < self.p50 < self.p100:
            raise ValueError("Thresholds must satisfy 0 < P50 < P100")

    def map(self, data: float) -> float:
        if isinstance(data, bool) or not isinstance(data, (int, float)) or not math.isfinite(data):
            raise ValueError("Mapping input must be a finite number")
        if data <= 0:
            return 0.0
        if data <= self.p50:
            return 50.0 * (data / self.p50)
        if data < self.p100:
            return 50.0 + 150.0 * ((data - self.p50) / (self.p100 - self.p50))
        return 200.0
