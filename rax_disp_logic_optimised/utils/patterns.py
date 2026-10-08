"""
Pre-compiled regex patterns and safe pattern builders.

All regex compilation happens at module load time (once).
Functions are stateless and fast.
"""

import re
from typing import List, Optional

from ..core.constants import (
    RE_ISOLATED_SPECIALS,
    RE_NORMALIZE_COL,
    RE_CELL_NOISE,
)


def strip_isolated_specials(s: str) -> str:
    """
    Remove special characters NOT attached to alphanumeric on both sides.

    Kept  : S5_01D  RT-TC  GATE/SIDING  POINT NORMAL
    Removed: _      _col0  A5__         ##text
    """
    return RE_ISOLATED_SPECIALS.sub('', str(s)).strip()


def norm_col(name: str) -> str:
    """Normalize column name for fuzzy matching (upper, strip spaces/hyphens)."""
    return RE_NORMALIZE_COL.sub('', str(name).strip().upper())


def safe_pattern(items: Optional[List[str]]) -> Optional[str]:
    """
    Build regex-safe alternation from literal strings.

    Each element is re.escape()'d so special chars like +, *, ?, .
    never produce a broken pattern. Returns None when the list is empty.
    """
    escaped = [
        re.escape(str(p))
        for p in (items or [])
        if str(p).strip()
    ]
    return '|'.join(escaped) if escaped else None


def clean_cell_noise(s: str) -> str:
    """Remove noise characters from a cell value."""
    return RE_CELL_NOISE.sub('', str(s))


_RE_TOKEN_DELIM = re.compile(r'[\n,]+')


def build_token_index(series) -> dict:
    """
    Build a reverse index: {token → set of row indices}.

    Eliminates repeated str.contains() calls in inner loops.
    O(n) build time, O(1) lookup per token.
    """
    index: dict = {}
    for idx, val in enumerate(series.tolist()):
        if val is None or str(val) in ('', 'nan', 'None'):
            continue
        for token in _RE_TOKEN_DELIM.split(str(val)):
            token = token.strip()
            if token:
                index.setdefault(token, set()).add(idx)
    return index


def build_exact_token_index(series) -> dict:
    """Same as build_token_index but preserves exact token case."""
    index: dict = {}
    for idx, val in enumerate(series.tolist()):
        if val is None or str(val) in ('', 'nan', 'None'):
            continue
        for token in _RE_TOKEN_DELIM.split(str(val)):
            token = token.strip()
            if token:
                index.setdefault(token, set()).add(idx)
    return index
