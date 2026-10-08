"""
Point formatting functions for TOC columns.

Handles: RT-PT-N, RT-PT-R, OV-PT-N, OV-PT-R, ISO point parsing.
"""

import re
import pandas as pd

# Point numbers: '101', '101/102', also 2- and 4-digit points such as '21/22'.
_RE_POINT_NUM = re.compile(r'(?<!\d)(\d{1,4}(?:/\d{1,4})?)(?!\d)')
# Isolation points keep the full crossover name: '111/112N' -> '111/112'.
_RE_ISO_PT = re.compile(r'\d+(?:/\d+)?[NR]')
_RE_ISO_TC = re.compile(r'\d+(?:/\d+)*[A-Z]*T')


def pt_format(toc: pd.DataFrame, pt_column: str, NR: str) -> pd.Series:
    """
    Format a point column: clean noise, extract 3-digit point numbers,
    append N/R suffix.

    Parameters
    ----------
    toc : pd.DataFrame
        TOC DataFrame.
    pt_column : str
        Column name (e.g. 'RT-PT-N').
    NR : str
        Suffix to append ('N', 'R', or '').

    Returns
    -------
    pd.Series
        Formatted point values.
    """
    if pt_column not in toc.columns:
        return pd.Series([''] * len(toc), index=toc.index, dtype=str)
    col_idx = toc.columns.get_loc(pt_column)

    def process_row(val):
        if pd.isna(val):
            return ""
        pt_i = [x.strip() for x in str(val).split(',') if x.strip()]
        pt_i = [', '.join(_RE_POINT_NUM.findall(s)) for s in pt_i]
        pt_i = [x.strip() + NR for x in pt_i if x.strip()]
        return ", ".join(pt_i)

    return toc.iloc[:, col_idx].apply(process_row).astype(str)


def iso_pt_format(toc: pd.DataFrame, pt_column: str) -> pd.Series:
    """
    Parse the ISO column into components:
      - Return: all point entries with suffix (e.g. "244N, 246R")
      - Sets toc['ISO-TC'], toc['ISO-PT-N'], toc['ISO-PT-R']
    """
    toc['ISO-TC'] = ''
    toc['ISO-PT-N'] = ''
    toc['ISO-PT-R'] = ''

    iso_clean = (
        toc[pt_column]
        .astype(str)
        .str.replace(r'[ ,\^]', '', regex=True)
        .fillna('')
    )

    pt_vals = []
    tc_vals = []
    ptn_vals = []
    ptr_vals = []

    for val in iso_clean:
        pts = _RE_ISO_PT.findall(val)
        tcs = _RE_ISO_TC.findall(val)
        pt_vals.append(', '.join(pts))
        tc_vals.append(', '.join(tcs))
        ptn_vals.append(', '.join(p[:-1] for p in pts if p[-1] == 'N'))
        ptr_vals.append(', '.join(p[:-1] for p in pts if p[-1] == 'R'))

    toc['ISO-TC'] = pd.Series(tc_vals, index=toc.index, dtype='str')
    toc['ISO-PT-N'] = pd.Series(ptn_vals, index=toc.index, dtype='str')
    toc['ISO-PT-R'] = pd.Series(ptr_vals, index=toc.index, dtype='str')
    return pd.Series(pt_vals, index=toc.index, dtype='str')


def iso_tc_fn(itoc: pd.DataFrame, iso_column: str) -> pd.Series:
    """Extract track-circuit entries from the ISO column."""
    toc = itoc.copy()
    toc['ISO'] = (
        toc['ISO']
        .astype(str)
        .str.replace(' ', '', regex=False)
        .str.replace('^', '', regex=False)
        .fillna('')
    )

    def _extract(val: str) -> str:
        tokens = (t.strip().upper() for t in val.split(',') if t.strip())
        tc = [t for t in tokens if 'T' in t and 'N' not in t and 'R' not in t]
        return ', '.join(tc)

    return (
        toc[iso_column]
        .astype(str)
        .apply(_extract)
        .astype('string')
        .str.replace('\n', '', regex=False)
    )
