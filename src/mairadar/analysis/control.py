"""Cooperative cancellation shared by the in-memory analysis stages."""

from collections.abc import Callable


class AnalysisCancelled(Exception):
    """Raised when the caller requests cancellation of ongoing analysis."""


CancellationCheck = Callable[[], None]


def no_cancellation() -> None:
    pass
