"""
TOC data checks.

The locking search can only find conflicts that the TOC describes.  These
checks list rows whose data looks incomplete, so that a missing track
circuit does not silently turn into a permitted simultaneous movement.
"""

import re
from typing import List

import pandas as pd

from .locking import _has_alnum, co_track_circuits

_RE_POINT = re.compile(r'(?<!\d)(\d{1,4}(?:/\d{1,4})?)(?!\d)')


def _points(val) -> List[str]:
    return _RE_POINT.findall(str(val))


def _tc_names(val) -> List[str]:
    """'107/108T, 03AT, DMAT' -> ['107', '108', '03A', 'DMA']"""
    out = []
    for tc in re.split(r'[,\s]+', str(val).upper()):
        tc = tc.strip()
        if tc.endswith('T'):
            out += tc[:-1].split('/')
    return out


def _switch_covered(point: str, tc_val) -> bool:
    names = _tc_names(tc_val)
    return any(end in names for end in point.split('/'))


def toc_data_checks(toc: pd.DataFrame) -> pd.DataFrame:
    """
    Return one row per finding: ROUTE, CHECK, DETAIL.

    `toc` is the formatted TOC (after toc_format, before ixl_fn).
    """
    rows = []
    rt_tc = co_track_circuits(toc)
    has_ov = [_has_alnum(n) or _has_alnum(r)
              for n, r in zip(toc['OV-PT-N'].fillna(''), toc['OV-PT-R'].fillna(''))]
    ov_tc = co_track_circuits(toc, 'OV-TC', need=has_ov)

    for k, r in toc.reset_index(drop=True).iterrows():
        route = str(r['FROM-TO'])
        frm = str(r['FROM']).upper()
        rt_pts = _points(r['RT-PT-N']) + _points(r['RT-PT-R'])
        ov_pts = _points(r['OV-PT-N']) + _points(r['OV-PT-R'])

        if not _has_alnum(rt_tc.iat[k]):
            if frm.startswith('CO'):
                rows.append((route, 'Calling-on route has no track circuits',
                             f'No S{frm[2:]} route to {r["TO"]} found to take them from'))
            elif rt_pts:
                rows.append((route, 'Route has points but no route track circuits', ', '.join(rt_pts)))
            continue

        missing = [p for p in rt_pts if not _switch_covered(p, rt_tc.iat[k])]
        if missing:
            rows.append((route, 'Route point has no track circuit in RT-TC',
                         ', '.join(missing) + '  (if the point is only for flank protection, '
                         'move it to ISO)'))
        if ov_pts and not _has_alnum(ov_tc.iat[k]):
            rows.append((route, 'Overlap points but no overlap track circuit (OV-TC)',
                         ', '.join(ov_pts)))

    return pd.DataFrame(rows, columns=['ROUTE', 'CHECK', 'DETAIL'])


def permitted_pair_checks(toc: pd.DataFrame, disp_col: str = 'NEW-DSP2') -> pd.DataFrame:
    """
    Flag permitted pairs where one route ends at the signal the other
    starts from, in the opposite direction (possible head-on approach that
    no shared track circuit proves either way).
    """
    names = toc['FROM-TO'].astype(str).tolist()
    info = {n: (str(f), str(t), str(d)) for n, f, t, d in
            zip(names, toc['FROM'], toc['TO'], toc['DIR'])}
    rows = []
    for i, cell in enumerate(toc[disp_col].fillna('').astype(str)):
        a = names[i]
        for b in (x.strip() for x in cell.split('\n')):
            if not b or b not in info:
                continue
            fa, ta, da = info[a]
            fb, tb, db = info[b]
            if da != db and (ta == fb or tb == fa):
                rows.append((f'{a} / {b}', 'Permitted pair approaches the other route\'s start '
                             'signal from the opposite direction',
                             f'{a} ends at {ta}, {b} ends at {tb}; check that the berth '
                             'track is in RT-TC'))
    return pd.DataFrame(rows, columns=['ROUTE', 'CHECK', 'DETAIL'])
