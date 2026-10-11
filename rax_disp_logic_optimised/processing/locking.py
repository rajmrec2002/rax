"""
IXL locking computation.

Core locking search algorithm with pre-built reverse indexes
for O(1) token lookup instead of per-row str.contains().
"""

import re
import logging
import pandas as pd
from typing import List, Set, Dict

from ..processing.point_format import iso_pt_format, iso_uncond, pt_format
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


_RE_POST = re.compile(r'^(?:S|CO|SH|A)(\d+[A-Z]*)$')


def post_key(signal) -> str:
    """
    Signal post of a route's starting signal.

    S5, CO5, SH5 and A5 are on the same post -> '5'.  Routes from the same
    post can never be set at the same time.  Returns '' if the name does
    not follow the S/CO/SH/A + number pattern.
    """
    m = _RE_POST.match(str(signal).strip().upper())
    return m.group(1) if m else ''


def _tc_list(val) -> List[str]:
    return [t.strip() for t in re.split(r'[,\s]+', str(val).upper()) if t.strip()
            and _has_alnum(t)]


def _berthing_of(tcs: List[str]) -> List[str]:
    """
    Berthing = the last track circuits of a route with no point number
    ('01AT, 01BT'; usually ascending in the DN direction).  A point track
    circuit ends it.  Track circuits named after a calling-on signal
    (CO46T: proved for calling-on clearance) are not berthing.
    """
    out: List[str] = []
    for t in reversed(tcs):
        if re.match(r'^CO\d', t):
            continue
        if re.search(r'\d{3,}', t):
            break
        out.insert(0, t)
    return out


def co_track_circuits(toc: pd.DataFrame, col: str = 'RT-TC', need=None) -> pd.Series:
    """
    Track-circuit column `col` with the post-to-post rule applied.

    S, SH, A and CO movements from one signal post to the next post on the
    same line run over the same track and points to the same berthing in
    rear of the next stop signal.  By convention the TOC gives no track
    circuits for CO and no berthing track circuits for SH, so:

    * a CO movement with `col` blank (or '_' / '-') takes the track circuits
      of the S movement between the same posts on the same line (else the
      first S movement between those posts);
    * for RT-TC, an SH or A movement keeps its own track circuits and gets
      those of that S movement it lacks (the berthing).  If there is no such
      S movement, it gets the berthing of a movement ending at the same next
      post on the same line: the last track circuits of its RT-TC that carry
      no point number (usually a pair such as 01AT, 01BT or 05T, 05AT);
      failing that, the berthing of any movement on the same line; and
      only then the line's track circuits by name (01T / 01AT / 01BT,
      never one with a point number).

    `need`, if given, is a boolean list: only CO rows marked True are
    filled (used for OV-TC: only CO rows that list overlap points).
    """
    frm = toc['FROM'].astype(str).str.strip().str.upper()
    to = toc['TO'].astype(str).str.strip().str.upper()
    unk = [un_key(u) for u in toc['UN'].tolist()]
    tcs = toc[col].fillna('').astype(str)
    need = [True] * len(toc) if need is None else list(need)
    pf = [post_key(f) for f in frm]
    pt = [post_key(t) for t in to]

    by_route: Dict[tuple, str] = {}
    by_posts: Dict[tuple, str] = {}
    for f, a, b, u, tc in zip(frm, pf, pt, unk, tcs):
        if f.startswith('S') and not f.startswith('SH') and a and _has_alnum(tc):
            by_route.setdefault((a, b, u), tc)
            by_posts.setdefault((a, b), tc)

    all_tcs: List[str] = []
    berth_line: Dict[tuple, List[str]] = {}       # (next post, line) -> berthing
    berth_any: Dict[str, List[str]] = {}          # line -> berthing
    if col == 'RT-TC':
        for tc in tcs:
            all_tcs += [t for t in _tc_list(tc) if t not in all_tcs]
        for f, b, u, tc in zip(frm, pt, unk, tcs):
            if b and not f.startswith('SH'):
                bt = _berthing_of(_tc_list(tc))
                if bt:
                    berth_line.setdefault((b, u), bt)
                    berth_any.setdefault(u, bt)

    def berthing(u: str) -> List[str]:
        line = re.sub(r'^0+', '', str(u))
        if not line:
            return []
        pat = re.compile(r'^0*' + re.escape(line) + r'[AB]?T$')    # 01T, 01AT, 01BT
        return [t for t in all_tcs if pat.match(t) and not re.search(r'\d{3,}', t)]

    out = tcs.tolist()
    for k, (f, a, b, u, tc) in enumerate(zip(frm, pf, pt, unk, tcs)):
        if f.startswith('CO') and need[k] and not _has_alnum(tc):
            out[k] = by_route.get((a, b, u)) or by_posts.get((a, b), '')
        elif col == 'RT-TC' and (f.startswith('SH') or re.match(r'^A\d', f)) and _has_alnum(tc):
            own = _tc_list(tc)
            ref = _tc_list(by_route.get((a, b, u), ''))
            if ref:
                full = ref if set(own) <= set(ref) else own + [t for t in ref if t not in own]
            else:
                if _berthing_of(own):
                    full = own                      # already ends on its berthing
                else:
                    bt = berth_line.get((b, u)) or berth_any.get(u) or berthing(u)
                    full = own + [t for t in bt if t not in own]
            if full != own:
                out[k] = ', '.join(full)
    return pd.Series(out, index=toc.index, dtype=str)


def berthing_track_circuits(toc: pd.DataFrame) -> pd.Series:
    """
    Berthing of each movement (in rear of its next stop signal post), as a
    comma-separated string.  From the end of its RT-TC (post-to-post rule
    applied); where that list ends on a point track circuit (out of order),
    the berthing of the other movements into the same post on the same line.
    Empty where none applies (e.g. shunts into sidings).
    """
    rt = co_track_circuits(toc)
    pt = [post_key(t) for t in toc['TO'].astype(str).str.strip().str.upper()]
    unk = [un_key(u) for u in toc['UN'].tolist()]
    own = [_berthing_of(_tc_list(v)) for v in rt]
    by_post_line: Dict[tuple, List[str]] = {}
    for b, u, bt in zip(pt, unk, own):
        if b and bt:
            by_post_line.setdefault((b, u), bt)
    out = []
    for b, u, bt, v in zip(pt, unk, own, rt):
        if not bt:
            ref = by_post_line.get((b, u), [])
            bt = [t for t in ref if t in _tc_list(v)]     # only if the route has them
        out.append(', '.join(bt))
    return pd.Series(out, index=toc.index, dtype=str)


def track_circuits_for_locking(toc: pd.DataFrame) -> pd.Series:
    """
    TCK column: route + overlap + isolation track circuits.

    Post-to-post rule (see co_track_circuits): CO routes take RT-TC from
    their S movement, SH / A routes get its berthing; CO routes take OV-TC
    only when the CO row itself lists overlap points (a calling-on route
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
    # pairwise locking uses only the unconditional isolation points; a
    # conditional one ('#139/140# with S33_39') applies only while that
    # other movement is set as well
    iso_n = toc['ISO-PT-N'].map(iso_uncond)
    iso_r = toc['ISO-PT-R'].map(iso_uncond)
    isopn_list = batch_split(iso_n.astype(str).str.strip(strip), ', ')
    isopr_list = batch_split(iso_r.astype(str).str.strip(strip), ', ')
    tck_list = batch_split(toc['TCK'].astype(str).str.strip(strip), ', ')
    un_list = [un_key(u) for u in toc['UN'].tolist()]
    post_list = [post_key(f) for f in toc['FROM'].tolist()]

    # ── Build reverse indexes for O(1) lookup ────────────────────────────
    ov_pt_r_idx = build_token_index(toc['OV-PT-R'])
    rt_pt_r_idx = build_token_index(toc['RT-PT-R'])
    iso_pt_r_idx = build_token_index(iso_r)
    rt_pt_n_idx = build_token_index(toc['RT-PT-N'])
    ov_pt_n_idx = build_token_index(toc['OV-PT-N'])
    iso_pt_n_idx = build_token_index(iso_n)
    tck_idx = build_exact_token_index(toc['TCK'])
    un_idx: Dict[str, Set[int]] = {}
    for idx, key in enumerate(un_list):
        if key:
            un_idx.setdefault(key, set()).add(idx)
    post_idx: Dict[str, Set[int]] = {}
    for idx, key in enumerate(post_list):
        if key:
            post_idx.setdefault(key, set()).add(idx)

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

        # Routes from signals on the same post (S5, CO5, SH5, A5) always
        # conflict with each other.
        if post_list[i]:
            matched_indices.update(post_idx.get(post_list[i], set()))

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

    # ISO display column: a point without a letter is Normal, so drop only
    # the N; a Reverse isolation point keeps its R ('147/148, 438R').
    toc['ISO'] = (
        toc['ISO'].astype(str)
        .str.replace(r'(?<=[0-9])N', '', regex=True)
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
