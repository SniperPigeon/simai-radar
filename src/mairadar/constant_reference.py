"""Versioned facts copied from the parser/player implementations we follow.

This module contains upstream reference data shared by parsing and analysis.  Tunable
analysis parameters and score calibration thresholds do not belong here.
"""

from types import MappingProxyType
from typing import Final, Mapping


MAJSIMAI_PIN: Final = "fdb2a3e39d8997a0abbf8b4679062d854473cc77"
MAJDATAPLAY_PIN: Final = "c3423a4bba536e53921e8fdedab2b9d91121b393"

# MajdataPlay NoteLoader uses the selected slide prefab's Transform.childCount as
# ``barCount`` when it divides a connected Slide's total duration between segments.
# SlideDrop also exposes the same value as SlideLength: it excludes the final SlideOK
# child from SlideBars and then adds one.  These are therefore player length units,
# not arrow sprite counts and not physical hand-travel distances.
#
# Keys are the canonical prefab names produced after relative-position normalization;
# mirrored forms use the same entry.  Sources at MAJDATAPLAY_PIN:
#   Assets/Scripts/Scenes/Game/NoteLoader.cs
#   Assets/Prefabs/Game/Slides/*.prefab
MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "line3": 14,
        "line4": 19,
        "line5": 20,
        "line6": 19,
        "line7": 14,
        "circle1": 64,
        "circle2": 8,
        "circle3": 16,
        "circle4": 24,
        "circle5": 32,
        "circle6": 40,
        "circle7": 48,
        "circle8": 56,
        "v1": 21,
        "v2": 20,
        "v3": 20,
        "v4": 20,
        "v6": 20,
        "v7": 20,
        "v8": 20,
        "ppqq1": 36,
        "ppqq2": 29,
        "ppqq3": 23,
        "ppqq4": 50,
        "ppqq5": 50,
        "ppqq6": 49,
        "ppqq7": 47,
        "ppqq8": 42,
        "pq1": 34,
        "pq2": 31,
        "pq3": 28,
        "pq4": 25,
        "pq5": 22,
        "pq6": 43,
        "pq7": 41,
        "pq8": 37,
        "s": 31,
        "wifi": 12,
        "L2": 33, #为1V75等大V星星使用，两个折点不同的情况会镜像来套用值
        "L3": 35,
        "L4": 33,
        "L5": 29,
    }
)

# The pinned NoteLoader maps both ``v1`` and the extended-slide prefab to slot 41,
# whose serialized Game.unity entry is ExtendSlide.  The value above deliberately
# follows the dedicated Star_V_1.prefab: its 21 children describe the intended
# 1v1 path, and SlideTables independently defines A1 -> B1 -> C -> B1 -> A1.


__all__ = [
    "MAJSIMAI_PIN",
    "MAJDATAPLAY_PIN",
    "MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS",
]
