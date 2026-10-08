"""
IXL locking computation.

Core locking search algorithm with pre-built reverse indexes
for O(1) token lookup instead of per-row str.contains().
"""

import re
import logging
import pandas as pd
from typing import List, Set, Dict

from ..core.constants import STRIP_PUNCTUATION
from ..processing.point_format import pt_format, iso_pt_format, iso_tc_fn
from ..processing.track_circuit import tc_format_fn
from ..utils.patterns import build_token_index, build_exact_token_index
from ..utils.text_cleaning import batch_split, batch_strip

logger = logging.getLogger(__name__)


def ltoc_fn(toc: pd.DataFrame, ltoc: pd.DataFrame, i: int) -> pd.DataFrame:
    """Write locking FROM-TO string back into toc at row i."""
    if ltoc.size > 0:
        ltoc = ltoc[['FROM', 'UN']].copy()
        ltoc['FROM-TO'] = ltoc['FROM'].astype(str) + '_' + ltoc['UN'].astype(str)
        ltoc.sort_values('FROM-TO', inplace=True)
        ltoc.drop_duplicates(subset='FROM-TO', inplace=True)
        ltoc['FROM-TO'] = ltoc['FROM-TO'].replace('\n', ', ', regex=True)
        toc.at[toc.index[i], 'NEW-LOCK'] = ltoc['FROM-TO'].to_string(index=False)
    else:
        toc.at[toc.index[i], 'NEW-LOCK'] = ''
    return toc


def ixl_fn(xtoc: pd.DataFrame, hs, end, l_no) -> pd.DataFrame:
    """
    Compute IXL locking for all rows.

    Uses pre-built reverse indexes for O(1) token lookup.
    """
    iso_tc_orig = xtoc['ISO-TC'].copy()
    toc = xtoc.copy()

    # Point and isolation formatting
    toc['OV-PT-N'] = pt_format(toc, 'OV-PT-N', '')
    toc['OV-PT-R'] = pt_format(toc, 'OV-PT-R', '')
    toc['RT-PT-N'] = pt_format(toc, 'RT-PT-N', '')
    toc['RT-PT-R'] = pt_format(toc, 'RT-PT-R', '')
    toc['ISO'] = iso_pt_format(toc, 'ISO')
    toc['ISO-TC'] = iso_tc_fn(toc, 'ISO')
    toc['ISO'] = iso_pt_format(toc, 'ISO')
    toc['TCK'] = tc_format_fn(toc)

    # ── Pre-extract all columns to native lists ───────────────────────────
    strip = STRIP_PUNCTUATION
    rtptn_list = batch_split(
        toc['RT-PT-N'].astype(str).str.strip(strip), ', '
    )
    rtptr_list = batch_split(
        toc['RT-PT-R'].astype(str).str.strip(strip), ', '
    )
    ovpn_list = batch_split(
        toc['OV-PT-N'].astype(str).str.strip(strip), ', '
    )
    ovpr_list = batch_split(
        toc['OV-PT-R'].astype(str).str.strip(strip), ', '
    )
    tck_list = batch_split(
        toc['TCK'].astype(str).str.strip(strip), ', '
    )
    iso_tc_list = [
        str(x).split(', ') for x in toc['ISO-TC'].tolist()
    ]
    un_list = toc['UN'].astype(str).str.strip(strip).tolist()

    # Parse ISO points into N/R components
    iso_clean = (
        toc['ISO'].astype(str)
        .str.replace(r'[ ,\^]', '', regex=True)
        .fillna('')
    )
    rm = re.compile(r'(NIL|#|\^|T|@|\*)')

    isopt_raw_list = []
    isopn_list = []
    isopr_list = []

    for val in iso_clean.tolist():
        tokens = rm.sub('', val).split(', ')
        tokens = [t.strip() for t in tokens if t.strip()]
        n_pts = [t.replace('N', '') for t in tokens if t.endswith('N')]
        r_pts = [t.replace('R', '') for t in tokens if t.endswith('R')]
        isopt_raw_list.append(tokens)
        isopn_list.append(n_pts)
        isopr_list.append(r_pts)

    # ── Build reverse indexes for O(1) lookup ────────────────────────────
    ov_pt_r_idx = build_token_index(toc['OV-PT-R'])
    rt_pt_r_idx = build_token_index(toc['RT-PT-R'])
    iso_pt_r_idx = build_token_index(toc['ISO-PT-R'])
    rt_pt_n_idx = build_token_index(toc['RT-PT-N'])
    ov_pt_n_idx = build_token_index(toc['OV-PT-N'])
    iso_pt_n_idx = build_token_index(toc['ISO-PT-N'])
    tck_idx = build_exact_token_index(toc['TCK'])
    un_idx = build_token_index(toc['UN'])

    n_rows = len(toc)

    for i in range(n_rows):
        rtpn = rtptn_list[i]
        rtpr = rtptr_list[i]
        ovpn = ovpn_list[i]
        ovpr = ovpr_list[i]
        isopn = isopn_list[i]
        isopr = isopr_list[i]
        tck_i = tck_list[i]
        iso_tck = iso_tc_list[i]
        un_btn = un_list[i]

        # Build combined token lists (strip N/R suffix for cross-column matching)
        ptn = [re.sub(r'[NR]$', '', p) for p in rtpn + ovpn + isopn]
        ptr = [re.sub(r'[NR]$', '', p) for p in rtpr + ovpr + isopr]
        tck_all = tck_i + iso_tck

        # Filter empty tokens
        ptn = [x for x in ptn if x]
        ptr = [x for x in ptr if x]
        tck_all = [x for x in tck_all if x]
        if not ptn:
            ptn = ['']
        if not ptr:
            ptr = ['']
        if not tck_all:
            tck_all = ['']

        # ── Use reverse indexes for O(1) lookup ──────────────────────────
        matched_indices: Set[int] = set()

        if ptr != [''] and ptn != ['']:
            for token in ptn:
                matched_indices.update(ov_pt_r_idx.get(token, set()))
                matched_indices.update(rt_pt_r_idx.get(token, set()))
                matched_indices.update(iso_pt_r_idx.get(token, set()))
            for token in ptr:
                matched_indices.update(rt_pt_n_idx.get(token, set()))
                matched_indices.update(ov_pt_n_idx.get(token, set()))
                matched_indices.update(iso_pt_n_idx.get(token, set()))
            for token in tck_all:
                matched_indices.update(tck_idx.get(token, set()))

        elif ptr != [''] and ptn == ['']:
            for token in ptr:
                matched_indices.update(rt_pt_n_idx.get(token, set()))
                matched_indices.update(ov_pt_n_idx.get(token, set()))
                matched_indices.update(iso_pt_n_idx.get(token, set()))
            for token in tck_all:
                matched_indices.update(tck_idx.get(token, set()))

        elif ptr == [''] and ptn != ['']:
            for token in ptn:
                matched_indices.update(ov_pt_r_idx.get(token, set()))
                matched_indices.update(rt_pt_r_idx.get(token, set()))
                matched_indices.update(iso_pt_r_idx.get(token, set()))
            for token in tck_all:
                matched_indices.update(tck_idx.get(token, set()))

        else:
            for token in tck_all:
                matched_indices.update(tck_idx.get(token, set()))

        # Always add UN matches
        matched_indices.update(un_idx.get(un_btn, set()))

        # Build ltoc from matched indices (single iloc call)
        if matched_indices:
            ltoc = toc.iloc[sorted(matched_indices)]
        else:
            ltoc = pd.DataFrame(columns=toc.columns)

        toc = ltoc_fn(toc, ltoc, i)

    # ── Reformat after IXL processing ────────────────────────────────────
    toc['OV-PT-N'] = pt_format(toc, 'OV-PT-N', 'N')
    toc['OV-PT-R'] = pt_format(toc, 'OV-PT-R', 'R')
    toc['RT-PT-N'] = pt_format(toc, 'RT-PT-N', 'N')
    toc['RT-PT-R'] = pt_format(toc, 'RT-PT-R', 'R')
    toc['ISO'] = iso_pt_format(toc, 'ISO')

    # Strip N/R suffixes from ISO display column
    toc['ISO'] = (
        toc['ISO'].astype(str)
        .str.replace(r'(?<=[0-9])[NR]', '', regex=True)
        .str.strip(', ')
    )

    toc['ISO-TC'] = iso_tc_orig
    toc['TCK'] = tc_format_fn(toc)

    return toc


def gen_disp_mvt(toc: pd.DataFrame, lck: str) -> pd.DataFrame:
    """
    Generate dispensation movements by excluding locked routes.

    For each row, collects all FROM-TO values NOT in the lock column.
    """
    lck_series = (
        toc[lck].fillna('').astype(str)
        .str.replace(r'\n', ',', regex=True)
        .str.replace(' ', '', regex=True)
    )

    new_disp_values = []

    for i, cell in enumerate(lck_series):
        tokens = [t for t in cell.split(',') if t]

        if tokens:
            pat = '|'.join(re.escape(t) for t in tokens)
            mask = ~toc['FROM-TO'].astype(str).str.contains(pat, na=False)
            dtoc = toc[mask]
        else:
            dtoc = toc

        z_series = dtoc['FROM-TO'].astype(str).str.replace(' ', '', regex=True)

        if z_series.empty:
            new_disp_values.append('')
            continue

        z = ','.join(z_series.tolist())

        if len(z) >= 3 and z[2] == 'r':
            new_disp_values.append('')
        else:
            new_disp_values.append(z)

    toc['NEW-DISP'] = pd.Series(new_disp_values, index=toc.index).fillna('')
    return toc
