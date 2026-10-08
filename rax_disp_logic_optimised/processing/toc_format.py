"""
TOC formatting – movement text, direction calculation, field assembly.
"""

import re
import numpy as np
import pandas as pd

from ..utils.text_cleaning import coerce_string_columns, clean_columns_vectorized

_FRTO_DEFAULT = "from"


def mvtx_fn(toc: pd.DataFrame) -> None:
    """Split MVT column into FROM and UN columns (vectorized)."""
    split = toc['MVT'].str.split('-', n=1, expand=True)
    toc['FROM'] = split[0]
    toc['UN'] = split[1]
    toc['GN'] = toc['FROM']


def toc_format(toc: pd.DataFrame, hs, end, l_n) -> pd.DataFrame:
    """
    Format TOC DataFrame: clean fields, compute direction,
    assign reception direction labels.
    """
    str_cols = [
        'FROM', 'TO', 'GN', 'RT-TC', 'OV-TC',
        'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R',
        'ISO', 'ISO-TC', 'UN', 'OV-SET', 'YR',
    ]
    for col in str_cols:
        if col in toc.columns:
            toc[col] = toc[col].astype(str).replace('nan', '')

    # Clean noise characters (vectorized)
    clean_cols = [
        'FROM', 'TO', 'GN', 'RT-TC', 'OV-TC',
        'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R',
        'ISO', 'ISO-TC', 'UN',
    ]
    toc = clean_columns_vectorized(toc, clean_cols)
    toc = toc.fillna("")

    # Insert comma separator in ISO for concatenated points
    toc['ISO'] = (
        toc['ISO'].astype(str)
        .str.replace(r'(?<=[0-9])[NR](?=[0-9])', lambda m: m.group() + ', ', regex=True)
        .str.replace(r',\s*,', ', ', regex=True)
        .str.strip(', ')
    )

    toc['FROM-TO'] = toc['FROM'].astype(str) + '_' + toc['UN'].astype(str)

    from ..processing.point_format import iso_tc_fn
    toc['ISO-TC'] = iso_tc_fn(toc, 'ISO')
    toc[['RT-TC', 'OV-TC', 'ISO-TC']] = (
        toc[['RT-TC', 'OV-TC', 'ISO-TC']].replace('/AXT', '', regex=True)
    )

    # Direction (fully vectorized)
    frm = toc['FROM'].astype(str).str.strip()
    last = frm.str[-1:]
    digit_mask = last.str.match(r'^\d$', na=False)
    digit_val = pd.to_numeric(last.where(digit_mask), errors='coerce').fillna(0).astype(int)
    toc['DIR'] = np.where(digit_mask, np.where(digit_val % 2 == 0, 'UP', 'DN'), frm.str[:2].values)

    toc['SH-DISP-LIT'] = ''
    toc['MN-MOVT-LIT'] = ''
    toc['SUB-MOVT-LIT'] = ''
    toc['END'] = ""

    # Map home-signal groups to reception direction
    hs_end_map = list(zip(hs, end))
    for hs_list, end_val in hs_end_map:
        if end_val:
            mask = toc['FROM-TO'].str.match('|'.join(hs_list), na=False)
            toc.loc[mask, "END"] = end_val

    end = [x for x in end if x != '']
    if end:
        end_pattern = '|'.join(end)
        toc.loc[toc['END'].str.contains(end_pattern, na=False), "RD"] = "received"
        toc.loc[toc['RD'] != "received", "RD"] = "dispatched"
    else:
        toc["RD"] = "dispatched"

    toc["FRTO"] = _FRTO_DEFAULT

    return toc
