"""
IXL locking computation.

Core locking search algorithm with pre-built reverse indexes
for O(1) token lookup instead of per-row str.contains().
"""

import re
import logging
import pandas as pd
from typing import List, Set, Dict

from ..processing.point_format import pt_format, iso_pt_format
from ..processing.track_circuit import tc_format_fn
from ..utils.patterns import build_token_index, build_exact_token_index
from ..utils.text_cleaning import batch_split

logger = logging.getLogger(__name__)

_LIST_STRIP = ' \t\n.,_()'
_RE_UN_LINE_SUFFIX = re.compile(r'(?<=\d)[DM]$')


def un_key(un) -> str:
    """
    Normalise a route/line number for same-line comparison.

    '01D', '01M', '1' -> '1';  'JKCL2D', 'JKCL2M' -> 'JKCL2';  'UMAB' -> 'UMAB'.
    """
    s = str(un).strip().upper()
    if s in ('', 'NAN', 'NONE'):
        return ''
    s = _RE_UN_LINE_SUFFIX.sub('', s)
    return s.lstrip('0') or s


_RE_ALNUM = re.compile(r'[A-Za-z0-9]')


def _has_alnum(val) -> bool:
    return bool(_RE_ALNUM.search(str(val)))


def co_track_circuits(toc: pd.DataFrame, col: str = 'RT-TC', need=None) -> pd.Series:
    """
    Track-circuit column `col` with calling-on routes filled in from their
    main route.

    A CO<n> route whose `col` is blank (or a placeholder such as '_' / '-')
    runs over the same track as the S<n> route to the same TO signal and
    line, so it takes that route's track circuits (exact UN match first,
    else the first S<n> route to the same TO).  `need`, if given, is a
    boolean list: only CO rows marked True are filled.  Other rows are
    returned unchanged.
    """
    frm = toc['FROM'].astype(str).str.strip().str.upper()
    to = toc['TO'].astype(str).str.strip().str.upper()
    unk = [un_key(u) for u in toc['UN'].tolist()]
    tcs = toc[col].fillna('').astype(str)
    need = [True] * len(toc) if need is None else list(need)

    by_route: Dict[tuple, str] = {}
    by_to: Dict[tuple, str] = {}
    for f, t, u, tc in zip(frm, to, unk, tcs):
        if f.startswith('S') and not f.startswith('SH') and _has_alnum(tc):
            by_route.setdefault((f, t, u), tc)
            by_to.setdefault((f, t), tc)

    out = tcs.tolist()
    for k, (f, t, u, tc) in enumerate(zip(frm, to, unk, tcs)):
        if f.startswith('CO') and need[k] and not _has_alnum(tc):
            main = 'S' + f[2:]
            out[k] = by_route.get((main, t, u)) or by_to.get((main, t), '')
    return pd.Series(out, index=toc.index, dtype=str)


def track_circuits_for_locking(toc: pd.DataFrame) -> pd.Series:
    """
    TCK column: route + overlap + isolation track circuits.

    CO routes inherit RT-TC from their S route; they inherit OV-TC only
    when the CO row itself lists overlap points (a calling-on route
    normally has no overlap).
    """
    tmp = toc[['RT-TC', 'OV-TC', 'ISO-TC']].copy()
    tmp['RT-TC'] = co_track_circuits(toc)
    has_ov = [_has_alnum(n) or _has_alnum(r)
              for n, r in zip(toc['OV-PT-N'].fillna(''), toc['OV-PT-R'].fillna(''))]
    tmp['OV-TC'] = co_track_circuits(toc, 'OV-TC', need=has_ov)
    return tc_format_fn(tmp)


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

    # Point and isolation formatting.  iso_pt_format() fills ISO-PT-N /
    # ISO-PT-R from the ISO column; the ISO track circuits were already
    # extracted by toc_format() and must be kept as they are (re-deriving
    # them from the points-only ISO column used to blank them out).
    toc['OV-PT-N'] = pt_format(toc, 'OV-PT-N', '')
    toc['OV-PT-R'] = pt_format(toc, 'OV-PT-R', '')
    toc['RT-PT-N'] = pt_format(toc, 'RT-PT-N', '')
    toc['RT-PT-R'] = pt_format(toc, 'RT-PT-R', '')
    toc['ISO'] = iso_pt_format(toc, 'ISO')
    toc['ISO-TC'] = iso_tc_orig
    toc['TCK'] = track_circuits_for_locking(toc)

    # ── Pre-extract all columns to native lists ───────────────────────────
    # Only whitespace/punctuation is stripped here: stripping letters such
    # as D/M would corrupt names like 'DMAT' (track) or '01D' (route).
    strip = _LIST_STRIP
    rtptn_list = batch_split(toc['RT-PT-N'].astype(str).str.strip(strip), ', ')
    rtptr_list = batch_split(toc['RT-PT-R'].astype(str).str.strip(strip), ', ')
    ovpn_list = batch_split(toc['OV-PT-N'].astype(str).str.strip(strip), ', ')
    ovpr_list = batch_split(toc['OV-PT-R'].astype(str).str.strip(strip), ', ')
    isopn_list = batch_split(toc['ISO-PT-N'].astype(str).str.strip(strip), ', ')
    isopr_list = batch_split(toc['ISO-PT-R'].astype(str).str.strip(strip), ', ')
    tck_list = batch_split(toc['TCK'].astype(str).str.strip(strip), ', ')
    un_list = [un_key(u) for u in toc['UN'].tolist()]

    # ── Build reverse indexes for O(1) lookup ────────────────────────────
    ov_pt_r_idx = build_token_index(toc['OV-PT-R'])
    rt_pt_r_idx = build_token_index(toc['RT-PT-R'])
    iso_pt_r_idx = build_token_index(toc['ISO-PT-R'])
    rt_pt_n_idx = build_token_index(toc['RT-PT-N'])
    ov_pt_n_idx = build_token_index(toc['OV-PT-N'])
    iso_pt_n_idx = build_token_index(toc['ISO-PT-N'])
    tck_idx = build_exact_token_index(toc['TCK'])
    un_idx: Dict[str, Set[int]] = {}
    for idx, key in enumerate(un_list):
        if key:
            un_idx.setdefault(key, set()).add(idx)

    n_rows = len(toc)

    for i in range(n_rows):
        # A point needed Normal here conflicts with any route needing it
        # Reverse (route, overlap or isolation), and vice versa.
        ptn = [x for x in rtptn_list[i] + ovpn_list[i] + isopn_list[i] if x]
        ptr = [x for x in rtptr_list[i] + ovpr_list[i] + isopr_list[i] if x]
        tck_all = [x for x in tck_list[i] if x]

        matched_indices: Set[int] = set()
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

        # Routes onto the same line (same UN, ignoring a trailing D/M and
        # leading zeros) always conflict.
        if un_list[i]:
            matched_indices.update(un_idx.get(un_list[i], set()))

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
    toc['TCK'] = track_circuits_for_locking(toc)

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
