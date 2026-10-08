"""
Text cleaning utilities for cell values, column names, and display strings.
"""

import re
import pandas as pd
from typing import List

from ..core.constants import (
    RE_CELL_NOISE,
    RE_TC_SUFFIX,
    RE_NON_ALNUM,
    RE_HAS_ALPHA,
    RE_HAS_DIGIT,
    CLEAN_PATTERNS,
    NR_PATTERNS,
    STRIP_PUNCTUATION,
)


def batch_strip(series: pd.Series, chars: str = STRIP_PUNCTUATION) -> List[str]:
    """
    Strip punctuation from every cell in a Series.
    Returns a native Python list for O(1) indexed access.
    """
    return [
        str(v).strip(chars) if v and str(v) not in ('nan', 'None') else ''
        for v in series.tolist()
    ]


def batch_split(series: pd.Series, sep: str = ', ') -> List[List[str]]:
    """
    Split every cell in a Series by separator into a list of strings.
    Returns a list of lists for O(1) indexed access.
    """
    return [
        [t.strip() for t in str(v).split(sep) if t.strip()]
        if v and str(v) not in ('', 'nan', 'None')
        else ['']
        for v in series.tolist()
    ]


def clean_columns_vectorized(
    df: pd.DataFrame,
    columns: List[str],
    patterns: List[str] = CLEAN_PATTERNS,
) -> pd.DataFrame:
    """
    Apply noise-removal regex patterns to specified columns.
    Fully vectorized – no row-by-row iteration.
    """
    pat = '|'.join(patterns)
    for col in columns:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(pat, '', regex=True)
            )
    return df


def coerce_string_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """
    Convert specified columns to string, replacing 'nan' with ''.
    """
    for col in columns:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .replace({'nan': '', 'None': '', 'none': ''})
            )
    return df


def calc_direction(frm: str) -> str:
    """
    Calculate movement direction from signal name.
    Odd last digit → DN, Even → UP.
    """
    frm = str(frm).strip()
    try:
        last_char = int(frm[-1])
        return "UP" if last_char % 2 == 0 else "DN"
    except (ValueError, IndexError):
        return frm[0:2] if len(frm) >= 2 else ''


def has_alphanumeric_content(series: pd.Series) -> pd.Series:
    """Check if each cell contains at least one letter or digit."""
    s = series.fillna('').astype(str)
    return s.str.contains(RE_HAS_ALPHA) & s.str.contains(RE_HAS_DIGIT)
