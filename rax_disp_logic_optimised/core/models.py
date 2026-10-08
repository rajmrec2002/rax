"""
Typed data structures for type safety and IDE support.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import pandas as pd


@dataclass
class HomeSignalGroups:
    """Result of auto_detect_home_signals()."""
    groups: List[List[str]] = field(
        default_factory=lambda: [[''], [''], [''], ['']]
    )
    ends: List[str] = field(
        default_factory=lambda: ['', '', '', '']
    )


@dataclass
class CrissCrossResult:
    """Output of criss_cross_mvt()."""
    toc: pd.DataFrame
    xx_pt_list: pd.DataFrame
    xx_mt_list: pd.DataFrame


@dataclass
class ChainageEntry:
    """Single chainage lookup result."""
    gr_no: str
    gr_ch: float
    found: bool
    source: str  # 'cache', 'file', 'prompt', 'fallback'


@dataclass
class LockingSearchResult:
    """Accumulator for IXL locking search."""
    matched_indices: set = field(default_factory=set)
    ltoc: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame()
    )


# Type aliases
PointList = List[str]
TrackCircuitList = List[str]
TokenIndex = Dict[str, set]
