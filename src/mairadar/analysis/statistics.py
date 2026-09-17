"""Small, deterministic summaries shared by analysis feature families."""

import math
from statistics import fmean, median, pstdev, quantiles


def summarize(values, prefix):
    values = tuple(values)
    if not values:
        stats = dict.fromkeys(("count", "mean", "std", "median", "max", "rms", "p95"), 0.0)
    else:
        stats = {
            "count": len(values), "mean": fmean(values), "std": pstdev(values),
            "median": median(values), "max": max(values),
            "rms": math.sqrt(fmean(value * value for value in values)),
            "p95": quantiles(values, n=20, method="inclusive")[-1] if len(values) > 1 else values[0],
        }
    return {f"{prefix}_{key}": value for key, value in stats.items()}
