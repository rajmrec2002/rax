"""
Criss-cross movement computation.

Performance-critical O(n²) loop. On Linux uses a multiprocessing fork-Pool
so all CPU cores work in parallel. On other platforms falls back to a
single-threaded loop (still runs in the GUI's background thread).
"""

import math
import os
import re
import platform
import logging
import multiprocessing as mp
import pandas as pd
from typing import Optional, Tuple

from ..core.constants import CLEAN_PATTERNS, NR_PATTERNS
from ..utils.patterns import safe_pattern
from ..utils.logging_util import debug_print

logger = logging.getLogger(__name__)

# ── Global context populated before forking ───────────────────────────────
_CRISS_CTX: dict = {}

_MISSING = float('nan')


# ── Local helpers ──────────────────────────────────────────────────────────

def _safe_label_prefix(val: str) -> str:
    s = str(val)
    return s.split('_')[0] if '_' in s else s


def _rm_dup(li) -> list:
    if not isinstance(li, list):
        return []
    seen: set = set()
    out = []
    for x in li:
        x = str(x).strip().replace('(', '').replace(')', '').strip()
        if 3 <= len(x) <= 7 and x not in seen:
            seen.add(x); out.append(x)
    return out


def _min_dist_val(text) -> float:
    ds = [float(d) for d in re.findall(r'is \(([\d\.]+)M\)', str(text))]
    return min(ds) if ds else 0.0


def _merge_iso_pts(rt_series: pd.Series, iso_series: pd.Series, suffix: str) -> pd.Series:
    result = []
    for rt, iso in zip(rt_series.astype(str), iso_series.astype(str)):
        existing = {p.strip() for p in rt.split(',') if p.strip()}
        extras = [p.strip() + suffix for p in iso.split(',')
                  if p.strip() and (p.strip() + suffix) not in existing]
        result.append(', '.join(p for p in ([rt.strip(', ')] + extras) if p))
    return pd.Series(result, index=rt_series.index, dtype='str')


def _resolve_ch(ch_df: pd.DataFrame, sig: str, cache: dict) -> float:
    """
    Silent 3-tier chainage lookup.

    Returns NaN when the signal/point is not in the chainage file.  NaN
    makes every distance computed from it NaN, which fails both the
    "within range" and "out of range" tests, so no distance remark is
    produced and the pair stays a conflict.  (Returning 0.0 here used to
    turn a missing chainage into a bogus distance that could pass.)
    """
    sig = str(sig).strip()
    if not sig:
        return _MISSING
    if sig in cache:
        return float(cache[sig])
    m = ch_df.loc[ch_df['GR_NO'] == sig, 'GR_CH']
    if not m.empty:
        v = float(m.iloc[-1]); cache[sig] = v; return v
    base = re.sub(r'[_/].*$', '', sig)
    if base and base != sig:
        if base in cache:
            cache[sig] = float(cache[base]); return float(cache[base])
        m2 = ch_df.loc[ch_df['GR_NO'] == base, 'GR_CH']
        if not m2.empty:
            v = float(m2.iloc[-1]); cache[sig] = v; return v
    else:
        base = sig
    nm = re.search(r'\d+', base)
    if nm:
        s_sig = 'S' + nm.group()
        if s_sig != sig:
            if s_sig in cache:
                cache[sig] = float(cache[s_sig]); return float(cache[s_sig])
            m3 = ch_df.loc[ch_df['GR_NO'] == s_sig, 'GR_CH']
            if not m3.empty:
                v = float(m3.iloc[-1]); cache[sig] = v; return v
    cache[sig] = _MISSING
    return _MISSING


def _get_pt_ch_val(ch_df: pd.DataFrame, pt_key: str, cache: dict):
    v = _resolve_ch(ch_df, pt_key, cache)
    return v, not math.isnan(v)


def _missing_chainage(toc: pd.DataFrame, ch_df: pd.DataFrame) -> list:
    """Signals and points used by the TOC that have no chainage entry."""
    keys = set()
    for col in ('FROM', 'TO'):
        keys.update(v for v in toc[col].astype(str).str.strip() if v)
    for col in ('RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R'):
        for val in toc[col].astype(str):
            keys.update('PT' + m for m in re.findall(r'(?<!\d)(\d{3})(?!\d)', val))
    cache: dict = {}
    return sorted(k for k in keys if math.isnan(_resolve_ch(ch_df, k, cache)))


def _pt_protecting_tc(point_str: str, from_sig: str) -> Optional[str]:
    parts = [p.strip().rstrip('T') for p in str(point_str).split('/')]
    if not parts[0]:
        return None
    if len(parts) == 1:
        return f'{parts[0]}T'
    try:
        n1, n2 = int(parts[0]), int(parts[1])
    except ValueError:
        return f'{parts[0]}T'
    sig_m = re.search(r'\d+', str(from_sig))
    sig_num = int(sig_m.group()) if sig_m else 0
    want_odd = (sig_num % 2 == 1)
    tc_num = (n1 if n1 % 2 == 1 else n2) if want_odd else (n1 if n1 % 2 == 0 else n2)
    return f'{tc_num}T'


# ── Extension helpers ─────────────────────────────────────────────────────

def tomvt(toc, to, ovptn, ovptr, yx_rtptn, yx_ovptn, dir, iso_ptn,
          iso_ptr=None, iso_tc=None):
    xtoc = toc.copy()
    cols = ['FROM', 'TO', 'RT-TC', 'OV-TC',
            'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R', 'ISO']
    rm = [' ', r'\.', '-', '#', '@', r'\*', r'\?', r'\^', r'\$', r'\(', r'\)']
    for col in cols:
        xtoc[col] = xtoc[col].astype(str)
        for p in rm:
            xtoc[col] = xtoc[col].str.replace(p, '', regex=True)

    yx_rtptn = [p for p in (yx_rtptn or []) if p]
    yx_ovptn = [p for p in (yx_ovptn or []) if p]
    rt_ov    = [p for p in (yx_rtptn + yx_ovptn) if p]
    ovptn    = [p for p in (ovptn   or []) if p]
    ovptr    = [p for p in (ovptr   or []) if p]
    iso_ptn  = [p for p in (iso_ptn or []) if p]
    iso_ptr  = [p for p in (iso_ptr or []) if p]
    iso_tc   = [p for p in (iso_tc  or []) if p]

    fp = safe_pattern(to) or '(?!)'
    mask = (xtoc['FROM'].str.fullmatch(fp, case=True)
            & xtoc['DIR'].str.fullmatch(re.escape(dir) if dir else '(?!)', case=False))
    if iso_ptn: mask &= ~xtoc['RT-PT-R'].str.contains(safe_pattern(iso_ptn), regex=True, na=False)
    if iso_ptr: mask &= ~xtoc['RT-PT-N'].str.contains(safe_pattern(iso_ptr), regex=True, na=False)
    if iso_tc:  mask &= ~xtoc['RT-TC'].str.contains(safe_pattern(iso_tc),    regex=True, na=False)
    if ovptn:   mask &= ~xtoc['RT-PT-R'].str.contains(safe_pattern(ovptn),   regex=True, na=False)
    if ovptr:   mask &= ~xtoc['RT-PT-N'].str.contains(safe_pattern(ovptr),   regex=True, na=False)
    if rt_ov:
        _p = safe_pattern(rt_ov)
        if _p:
            mask &= ~xtoc['RT-PT-R'].str.contains(_p, regex=True, na=False)
            mask &= ~xtoc['OV-PT-R'].str.contains(_p, regex=True, na=False)

    xtoc = xtoc[mask].copy()
    if xtoc.empty:
        return [[''], [''], [''], [''], [''], ['']]

    for col in ['FROM', 'TO', 'RT-TC', 'OV-TC',
                'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R']:
        for p in rm + ['\n']:
            xtoc[col] = xtoc[col].str.replace(p, '', regex=True)

    def _su(s): return list(set(t for t in ','.join(s.astype(str).tolist()).split(',') if t))
    return ([list(set(xtoc['TO'].tolist()))]
            + [_su(xtoc[c]) for c in ['RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R', 'RT-TC']]
            ) or [[''], [''], [''], [''], [''], ['']]


def to_2mvt(toc, xto, rtptn, rtptr, ovptn, ovptr, dir, yf,
            iso_ptn=None, iso_ptr=None, iso_tc=None):
    empty = [[''], [''], [''], [''], [''], ['']]
    if not xto or xto == ['S00']:
        return empty
    xtoc = toc.copy()
    rt_ov   = [p for p in ((rtptn or []) + (ovptn or [])) if p]
    iso_ptn = [p for p in (iso_ptn or []) if p]
    iso_ptr = [p for p in (iso_ptr or []) if p]
    iso_tc  = [p for p in (iso_tc  or []) if p]

    fp = safe_pattern(xto) or '(?!)'
    rp = safe_pattern(rt_ov)
    mask = (xtoc['FROM'].str.fullmatch(fp, case=True)
            & xtoc['DIR'].str.contains(re.escape(dir) if dir else '(?!)', regex=True)
            & ~xtoc['TO'].str.fullmatch(re.escape(yf) if yf else '(?!)', case=True))
    if rp:
        mask &= (~xtoc['RT-PT-R'].str.contains(rp, regex=True, na=False)
                 & ~xtoc['OV-PT-R'].str.contains(rp, regex=True, na=False))
    if iso_ptn: mask &= ~xtoc['RT-PT-R'].str.contains(safe_pattern(iso_ptn), regex=True, na=False)
    if iso_ptr: mask &= ~xtoc['RT-PT-N'].str.contains(safe_pattern(iso_ptr), regex=True, na=False)
    if iso_tc:  mask &= ~xtoc['RT-TC'].str.contains(safe_pattern(iso_tc),    regex=True, na=False)

    xtoc = xtoc[mask].copy()
    if xtoc.empty:
        return empty

    rm = [' ', r'\.', '-', '#', '@', r'\*', r'\?', r'\^', r'\$', r'\(', r'\)']
    for col in ['FROM', 'TO', 'RT-TC', 'OV-TC',
                'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R']:
        xtoc[col] = xtoc[col].astype(str)
        for p in rm:
            xtoc[col] = xtoc[col].str.replace(p, '', regex=True)
        xtoc[col] = xtoc[col].str.replace('\n', ',', regex=True)

    def _su(s): return list(set(t for t in ','.join(s.astype(str).tolist()).split(',') if t))
    return ([list(set(xtoc['TO'].tolist()))]
            + [_su(xtoc[c]) for c in ['RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R', 'RT-TC']])


def find_all_extensions(toc, base_to, dir, iso_ptn, iso_ptr, iso_tc,
                        own_ovptn, own_ovptr, own_rtptn, own_rtptr, exclude_to=''):
    lv = [tomvt(toc, base_to, own_ovptn, own_ovptr, own_rtptn, own_ovptn,
                dir, iso_ptn, iso_ptr, iso_tc)]
    for _ in range(4):
        lv.append(to_2mvt(toc, lv[-1][0], own_rtptn, own_rtptr, own_ovptn, own_ovptr,
                          dir, exclude_to, iso_ptn, iso_ptr, iso_tc))
    return [sum((lv[k][col] for k in range(5)), []) for col in range(6)]


# ── Worker (module-level so fork-Pool can reference it) ──────────────────

def _criss_row_worker(i: int) -> list:
    """Compute all (i, j>=i) collision pairs for row i. Returns list of tuples."""
    ctx        = _CRISS_CTX
    toc        = ctx['toc']
    td         = ctx['toc_data']
    ch         = ctx['ch']
    sv         = ctx['sqsh_vals']     # sqsh.values.tolist()
    sq_idx     = ctx['sqsh_index']
    sq_col     = ctx['sqsh_cols']
    mn         = ctx['cc_dist_min']
    mx         = ctx['cc_dist_max']
    l          = ctx['l']
    cache: dict = {}
    results    = []

    for j in range(i, l):
        cv = ft_v = ''
        is_ft = False

        ni_sig  = td['TO'][i]
        ni_key  = _safe_label_prefix(sq_col[j])
        nj_key  = _safe_label_prefix(sq_idx[i])

        if (re.sub(r'[a-zA-Z]', '', str(ni_sig)) == re.sub(r'[a-zA-Z]', '', ni_key)
                or re.sub(r'[a-zA-Z]', '', str(td['TO'][j])) == re.sub(r'[a-zA-Z]', '', nj_key)):
            msg  = f'{sq_idx[i]} and {sq_col[j]} are from_to Movements'
            cv   = msg; ft_v = msg; is_ft = True
        elif i != j and len(str(sv[i][j])) == 0:
            q = q1 = q2 = q3 = q4 = q5 = q6 = q7 = q8 = q9 = q10 = 0
            xw = xw1 = xw2 = xw3 = xw4 = xw5 = xw6 = xw7 = xw8 = xw9 = xw10 = 0
            d1 = d2 = d3 = d4 = d5 = d6 = d7 = d8 = d9 = d10 = 0.0
            d1l = d2l = d3l = d4l = d5l = d6l = d7l = d8l = d9l = d10l = ''
            yovptr_s: list = []; xovptr_s: list = []   # populated in d5-d6 block

            xft = str(sq_idx[i]); yft = str(sq_col[j])
            if xft[0:2] != 'SH':
                xf = td['FROM'][i]; yf = td['FROM'][j]
                xdir = td['DIR'][i]; ydir = td['DIR'][j]
                xto = td['TO'][i];   yto = td['TO'][j]

                xSCH = [v + re.sub(r'[A-Za-z]', '', xto)
                        for v in ('S', 'CO', 'A', 'SH')
                        if v + re.sub(r'[A-Za-z]', '', xto) in td['_gn_set']]
                ySCH = [v + re.sub(r'[A-Za-z]', '', yto)
                        for v in ('S', 'CO', 'A', 'SH')
                        if v + re.sub(r'[A-Za-z]', '', yto) in td['_gn_set']]

                xovtc  = td['OV-TC'][i].split(',');    yovtc  = td['OV-TC'][j].split(',')
                xovptn = td['OV-PT-N'][i].split(',');  yovptn = td['OV-PT-N'][j].split(',')
                xrtptn = td['RT-PT-N'][i].split(',');  yrtptn = td['RT-PT-N'][j].split(',')
                xovptr = td['OV-PT-R'][i].split(',');  yovptr = td['OV-PT-R'][j].split(',')
                xrtptr = td['RT-PT-R'][i].split(',');  yrtptr = td['RT-PT-R'][j].split(',')
                xisoptn = td['ISO-PT-N'][i].split(','); yisoptn = td['ISO-PT-N'][j].split(',')
                xisoptr = td['ISO-PT-R'][i].split(','); yisoptr = td['ISO-PT-R'][j].split(',')
                xisotc  = td['ISO-TC'][i].split(',');   yisotc  = td['ISO-TC'][j].split(',')

                def _rttc(from_sig, to_sig, fallback_i):
                    _s = toc.loc[(toc['FROM'] == from_sig) & (toc['TO'] == to_sig), 'RT-TC']
                    return _s.iloc[0].split(',') if not _s.empty else []

                if 'CO' in xf and 'CO' in yf:
                    xrttc = _rttc(xf.replace('CO','S'), xto, i)
                    yrttc = _rttc(yf.replace('CO','S'), yto, j)
                elif 'CO' in xf:
                    xrttc = _rttc(xf.replace('CO','S'), xto, i)
                    yrttc = td['RT-TC'][j].split(',')
                elif 'CO' in yf:
                    yrttc = _rttc(yf.replace('CO','S'), yto, j)
                    xrttc = td['RT-TC'][i].split(',')
                elif 'A' in xf and 'A' in yf:
                    xrttc = _rttc(xf.replace('A','S'), xto, i)
                    yrttc = _rttc(yf.replace('A','S'), yto, j)
                elif 'A' in xf:
                    xrttc = _rttc(xf.replace('A','S'), xto, i)
                    yrttc = td['RT-TC'][j].split(',')
                elif 'A' in yf:
                    yrttc = _rttc(yf.replace('A','S'), yto, j)
                    xrttc = td['RT-TC'][i].split(',')
                elif 'SH' in yf and 'SH' in yto:
                    _ys = toc.loc[(toc['FROM'] == yf.replace('SH','S')) & (toc['TO'] == yto.replace('SH','S')), 'RT-TC']
                    if not _ys.empty:
                        yrttc = _ys.iloc[0].split(',')
                    else:
                        _ys2 = toc.loc[(toc['FROM'] == yf) & (toc['TO'] == yto), 'RT-TC']
                        yrttc = _ys2.iloc[0].split(',') if not _ys2.empty else []
                    xrttc = td['RT-TC'][i].split(',')
                else:
                    xrttc = td['RT-TC'][i].split(',')
                    yrttc = td['RT-TC'][j].split(',')

                xpt = [a for a in xrtptn + xrtptr if a]
                ypt = [a for a in yrtptn + yrtptr if a]
                xovptn = _rm_dup(xovptn); xovptr = _rm_dup(xovptr)
                xrtptn = _rm_dup(xrtptn); xrtptr = _rm_dup(xrtptr)
                yovptn = _rm_dup(yovptn); yovptr = _rm_dup(yovptr)
                yrtptn = _rm_dup(yrtptn); yrtptr = _rm_dup(yrtptr)

                xext = find_all_extensions(toc, xSCH, xdir,
                                           xisoptn, xisoptr, xisotc,
                                           xovptn, xovptr, xrtptn, xrtptr, yf)
                yext = find_all_extensions(toc, ySCH, ydir,
                                           yisoptn, yisoptr, yisotc,
                                           yovptn, yovptr, yrtptn, yrtptr, xf)

                x_to = [s.strip() for s in [xto] + [s for s in xext[0] if s] if s]
                y_to = [s.strip() for s in [yto] + [s for s in yext[0] if s] if s]

                xrtptn = xext[1] + xrtptn;  xrtptr = xrtptr + xext[2]
                yrtptn = yext[1] + yrtptn;  yrtptr = yrtptr + yext[2]
                xovptn = xext[3] + xovptn;  xovptr = xovptr + xext[4]
                yovptn = yext[3] + yovptn;  yovptr = yovptr + yext[4]
                x_tc = _rm_dup([s.strip() for s in xrttc + xext[5] if s])
                y_tc = _rm_dup([s.strip() for s in yrttc + yext[5] if s])

                xptr = [x.strip() for x in xrtptr + xovptr if x]
                yptr = [x.strip() for x in yrtptr + yovptr if x]
                xptn = [x.strip() for x in xrtptn + xovptn if x]
                yptn = [x.strip() for x in yrtptn + yovptn if x]

                xptr_n = ([x.strip() for x in xptr if x]
                          + [x.strip() for x in xptn if len(x) == 3])
                yptr_n = ([x.strip() for x in yptr if x]
                          + [x.strip() for x in yptn if len(x) == 3])
                xptr_n = [re.sub(r'[A-Z]', '', x) for x in xptr_n]
                xptn   = [re.sub(r'[A-Z]', '', x) for x in xptn]
                yptr_n = [re.sub(r'[A-Z]', '', x) for x in yptr_n]
                yptn   = [re.sub(r'[A-Z]', '', x) for x in yptn]

                cx1 = [y for y in yptr_n if y in xptn and y and y[0].isdigit()]
                cy1 = [x for x in xptr_n if x in yptn and x and x[0].isdigit()]
                cxt1 = _rm_dup([t for t in x_tc if t in yrttc and len(re.sub(r'[a-zA-Z]','',t)) >= 3])
                cyt1 = _rm_dup([t for t in y_tc if t in xrttc and len(re.sub(r'[a-zA-Z]','',t)) >= 3])

                def _dir_filter(lst, d):
                    return [x for x in lst if (len(str(x)) > 3)
                            or (int(x) % 2 == 0 if d == 'DN' else int(x) % 2 != 0)]
                cy1 = sorted(_rm_dup([x.strip() for x in cy1 if x]))
                cy1 = _dir_filter(cy1, ydir)
                cx1 = sorted(_rm_dup([x.strip() for x in cx1 if x]))
                cx1 = _dir_filter(cx1, xdir)

                xpt = [x.strip() for x in xpt if x]
                ypt = [x.strip() for x in ypt if x]

                # ── d1-d4: point collision ──────────────────────────────
                def _pt_dist(sig, pt_str, xt2_src, xt2_fallback, cache):
                    xt2 = re.sub('[A-Z]', '', xt2_src)
                    try: int(xt2)
                    except ValueError: xt2 = re.sub('[A-Z]', '', xt2_fallback)
                    s_ch = _resolve_ch(ch, sig, cache)
                    if int(xt2) % 2 != 0:
                        p_ch, fnd = _get_pt_ch_val(ch, 'PT' + pt_str[0:3], cache)
                        key = pt_str[0:3]
                    elif len(pt_str) > 6:
                        p_ch, fnd = _get_pt_ch_val(ch, 'PT' + pt_str[4:7], cache)
                        key = pt_str[4:7]
                    else:
                        p_ch, fnd = _get_pt_ch_val(ch, 'PT' + pt_str[0:3], cache)
                        if fnd:
                            p_ch = (p_ch + 98) if p_ch < 0 else (p_ch - 98)
                        key = pt_str[0:3]
                    return s_ch, p_ch, key, fnd

                if cx1:
                    if cy1 and xw == 0 and q == 0:
                        cx1s = sorted(cx1, reverse=(xdir != 'DN'))
                        for x in cx1s:
                            if x in ypt and xw1 == 0 and q1 == 0:
                                s_ch, p_ch, xk, fnd = _pt_dist(xto, x, xto, xf, cache)
                                if not fnd: continue
                                d1 = abs(round(s_ch - p_ch, 2))
                                if d1 > mn and d1 <= mx:
                                    d1l = f'[Dist. from {xto} (CH-{round(abs(s_ch),2)}) to FM of Point No.-{xk} (CH-{round(abs(p_ch),2)}) is ({d1}M)]'
                                    cv = d1l; q1 = 1
                                else:
                                    cv = ''; xw1 = 1
                        cy1s = sorted(cy1, reverse=(ydir != 'DN'))
                        for y in cy1s:
                            if y in xpt and xw2 + q2 == 0:
                                s_ch, p_ch, yk, fnd = _pt_dist(yto, y, yto, yf, cache)
                                if not fnd: continue
                                d2 = abs(round(s_ch - p_ch, 2))
                                if d2 > mn and d2 <= mx and q1 == 1:
                                    d2l = f'[Dist. from {yto} (CH-{round(abs(s_ch),2)}) to FM of Point No.-{yk} (CH-{round(abs(p_ch),2)}) is ({d2}M)]'
                                    cv = d1l + ' & ' + d2l; q2 = 1
                                elif d2 > mn and d2 <= mx and q1 == 0:
                                    d2l = f'[Dist. from {yto} (CH-{round(abs(s_ch),2)}) to FM of Point No.-{yk} (CH-{round(abs(p_ch),2)}) is ({d2}M)]'
                                    cv = d2l; q2 = 1
                                elif d2 < mn or d2 > mx:
                                    cv = ''; xw2 = 1
                    else:
                        cx1s = sorted(cx1, reverse=(xdir != 'DN'))
                        for x in cx1s:
                            if x in ypt and xw3 == 0 and q3 == 0:
                                s_ch, p_ch, xk, fnd = _pt_dist(xto, x, xto, xf, cache)
                                if not fnd: continue
                                d3 = abs(round(s_ch - p_ch, 2))
                                if d3 > mn and d3 <= mx:
                                    d3l = f'[Dist. from {xto} (CH-{round(abs(s_ch),2)}) to FM of Point No.-{xk} (CH-{round(abs(p_ch),2)}) is ({d3}M)]'
                                    cv = d3l; q3 = 1
                                else:
                                    cv = ''; xw3 = 1
                elif cy1:
                    cy1s = sorted(cy1, reverse=(ydir != 'DN'))
                    for y in cy1s:
                        if y in xpt and xw4 == 0 and q4 == 0:
                            s_ch, p_ch, yk, fnd = _pt_dist(yto, y, yto, yf, cache)
                            if not fnd: continue
                            d4 = abs(round(s_ch - p_ch, 2))
                            if d4 > mn and d4 <= mx:
                                d4l = f'[Dist. from {yto} (CH-{round(abs(s_ch),2)}) to FM of Point No.-{yk} (CH-{round(abs(p_ch),2)}) is ({d4}M)]'
                                cv = d4l; q4 = 1
                            else:
                                cv = ''; xw4 = 1

                # ── d5-d6: front collision ──────────────────────────────
                if xw1 + xw2 + xw3 + xw4 == 0:
                    _y_pt_tcs = {_pt_protecting_tc(p, yf) for p in (yptn + yptr) if p.strip()} - {None}
                    _x_pt_tcs = {_pt_protecting_tc(p, xf) for p in (xptn + xptr) if p.strip()} - {None}
                    yrttc_wo  = [s for s in yrttc if s.strip() not in _y_pt_tcs]
                    xrttc_wo  = [s for s in xrttc if s.strip() not in _x_pt_tcs]
                    yovtc_s   = [s.strip() for s in yovtc if s]
                    xovtc_s   = [s.strip() for s in xovtc if s]
                    yovptn_s  = [s.strip() for s in yovptn if s]
                    yovptr_s  = [s.strip() for s in yovptr if s]
                    xovptn_s  = [s.strip() for s in xovptn if s]
                    xovptr_s  = [s.strip() for s in xovptr if s]

                    def _front(sig_a, sig_b, dir_a, a_tc, b_rttc_wo, b_ovtc, b_ovptr, a_ptn, b_ovptn, a_ptr):
                        if (xdir == ydir): return 0.0, 0.0
                        if not (all(s in a_tc for s in b_rttc_wo if b_rttc_wo)
                                and all(s in a_tc for s in b_ovtc if b_ovtc)
                                and (True if not b_ovptr else any(s not in a_ptn for s in b_ovptr))
                                and (True if not b_ovptn else any(s not in a_ptr for s in b_ovptn))):
                            return 0.0, 0.0
                        return _resolve_ch(ch, sig_a, cache), _resolve_ch(ch, sig_b, cache)

                    xch5, ych5 = _front(xto, yto, xdir, x_tc, yrttc_wo, yovtc_s, yovptr_s, xptn, yovptn_s, xptr)
                    if xch5 != 0.0 or ych5 != 0.0:
                        d5 = abs(round(xch5 - ych5, 2))
                        if (d5 > mn and d5 <= mx
                            and ((xch5 > ych5 and xdir == 'DN') or (xch5 < ych5 and xdir == 'UP'))):
                            _b5 = f'[Dist. from Signal {xto} (CH-{round(abs(xch5),2)}) to Signal {yto} (CH-{round(abs(ych5),2)}) is ({d5}M)]'
                            if cv == '':
                                d5l = _b5; cv = d5l; q5 = 1
                            elif cv != '' and len(d1l + d3l) == 0:
                                d5l = _b5; cv = (cv + ' & ' + d5l) if cv else d5l; q5 = 1
                            elif (len(d1l + d3l) != 0
                                  and (valid := [v for v in (d1, d3) if v > 0])
                                  and d5 < min(valid)):
                                d5l = _b5
                                if len(d2l) > 0: cv = d2l + ' & ' + d5l
                                elif len(d4l) > 0: cv = d4l + ' & ' + d5l
                                elif (valid := [v for v in (d1, d3) if v > 0]) and d5 < min(valid): cv = d5l
                                q5 = 1
                        elif d5 < mn or d5 > mx:
                            cv = ''; xw5 = 1

                    xch6, ych6 = _front(yto, xto, ydir, y_tc, xrttc_wo, xovtc_s, xovptr_s, yptn, xovptn_s, yptr)
                    if xch6 != 0.0 or ych6 != 0.0:
                        d6 = abs(round(xch6 - ych6, 2))
                        if (d6 > mn and d6 <= mx
                            and ((ych6 > xch6 and ydir == 'DN') or (ych6 < xch6 and ydir == 'UP'))):
                            _b6 = f'[Dist. from Signal {yto} (CH-{round(abs(ych6),2)}) to Signal {xto} (CH-{round(abs(xch6),2)}) is ({d6}M)]'
                            if cv == '':
                                d6l = _b6; cv = d6l; q6 = 1
                            elif cv != '' and len(d2l + d4l) == 0:
                                d6l = _b6; cv = (cv + ' & ' + d6l) if cv else d6l; q6 = 1
                            elif (valid := [v for v in (d2, d4) if v > 0]) and d6 < min(valid):
                                d6l = _b6
                                if d1 > 0: cv = d1l + ' & ' + d6l
                                elif d3 > 0: cv = d3l + ' & ' + d6l
                                elif d5 > 0: cv = d5l + ' & ' + d6l
                                elif (valid := [v for v in (d2, d4) if v > 0]) and d6 < min(valid): cv = d6l
                                q6 = 1
                        elif d6 < mn or d6 > mx:
                            cv = ''; xw6 = 1

                # ── d7-d8: OV-point busting ─────────────────────────────
                if xw1 + xw2 + xw3 + xw4 + xw5 + xw6 == 0 and xdir != ydir:
                    for y in yovptr_s:
                        if y in xptn and q7 + xw7 == 0:
                            xch7 = _resolve_ch(ch, xto, cache)
                            ych7 = _resolve_ch(ch, yto, cache)
                            d7 = abs(round(xch7 - ych7, 2))
                            if d7 > mn and d7 <= mx:
                                _b7 = f'[Dist. from Signal {xto} (CH-{round(abs(xch7),2)}) to Signal {yto} (CH-{round(abs(ych7),2)}) is ({d7}M)]'
                                if cv == '':
                                    d7l = _b7; cv = d7l; q7 = 1
                                elif (valid := [v for v in (d1, d3, d5) if v > 0]) and d7 < min(valid):
                                    d7l = 'if OV point ' + y + ' busted, ' + _b7
                                    if d2 > 0: cv = d2l + ' & ' + d7l
                                    elif d4 > 0: cv = d4l + ' & ' + d7l
                                    elif d6 > 0: cv = d6l + ' & ' + d7l
                                    elif (valid := [v for v in (d1, d3, d5) if v > 0]) and d7 < min(valid): cv = d7l
                                    q7 = 1
                            elif d7 < mn or d7 > mx:
                                if q1+q2+q3+q4+q5+q6 == 0: cv = ''
                                xw7 = 1

                    for x in xovptr_s:
                        if x in yptn and q8 + xw8 == 0:
                            xch8 = _resolve_ch(ch, xto, cache)
                            ych8 = _resolve_ch(ch, yto, cache)
                            d8 = abs(round(ych8 - xch8, 2))
                            if d8 > mn and d8 <= mx:
                                _b8 = f'[Dist. from Signal {yto} (CH-{round(abs(ych8),2)}) to Signal {xto} (CH-{round(abs(xch8),2)}) is ({d8}M)]'
                                if cv == '':
                                    d8l = _b8; cv = d8l; q8 = 1
                                elif (d2+d4+d6) > 0 and d8 < _min_dist_val(d2l+d4l+d6l):
                                    d8l = 'if OV point ' + x + ' busted, ' + _b8
                                    if d1 > 0: cv = d1l + ' & ' + d8l
                                    elif d3 > 0: cv = d3l + ' & ' + d8l
                                    elif d5 > 0: cv = d5l + ' & ' + d8l
                                    elif d7 > 0: cv = d7l + ' & ' + d8l
                                    elif (valid := [v for v in (d2,d4,d6) if v > 0]) and d8 < min(valid): cv = d8l
                                    q8 = 1
                            elif d8 < mn or d8 > mx:
                                if q1+q2+q3+q4+q5+q6+q7 == 0: cv = ''
                                xw8 = 1

                # ── d9-d10: back-to-back ────────────────────────────────
                elif xw5+xw6+xw7+xw8 == 0 and xdir == ydir:
                    yf_n  = re.sub(r'[a-zA-Z]', '', yf)
                    x_to_n = [re.sub(r'[a-zA-Z]', '', s) for s in x_to]
                    if xdir == 'DN' and yf_n in x_to_n and xw9 + q9 == 0:
                        xch9 = _resolve_ch(ch, xto, cache)
                        yf_ch = _resolve_ch(ch, yf,  cache)
                        d9 = abs(round(yf_ch - xch9, 2))
                        if (d9 > mn and d9 <= mx
                            and ((xch9 > yf_ch and xdir == 'DN') or (xch9 < yf_ch and xdir == 'UP'))):
                            _b9 = f'[Dist. from Signal {xto} (CH-{round(abs(xch9),2)}) to Signal {yf} (CH-{round(abs(yf_ch),2)}) is ({d9}M)]'
                            if cv == '':
                                d9l = _b9; cv = d9l; q9 = 1
                            elif (d1+d3+d5+d7) > 0 and d9 < _min_dist_val(d1l+d3l+d5l+d7l):
                                d9l = _b9
                                if d2l: cv = d2l + ' & ' + d9l
                                elif d4l: cv = d4l + ' & ' + d9l
                                elif d6l: cv = d6l + ' & ' + d9l
                                elif d8l: cv = d8l + ' & ' + d9l
                                elif (valid := [v for v in (d1,d3,d5,d7) if v > 0]) and d9 < min(valid): cv = d9l
                                q9 = 1
                        elif d9 < mn or d9 > mx:
                            if q1+q2+q3+q4+q5+q6+q7+q8 == 0: cv = ''
                            xw9 = 1

                    xf_n  = re.sub(r'[a-zA-Z]', '', xf)
                    y_to_n = [re.sub(r'[a-zA-Z]', '', s) for s in y_to]
                    if xdir == 'UP' and xf_n in y_to_n and xw10 + q10 == 0:
                        xf_ch  = _resolve_ch(ch, xf,  cache)
                        ych10  = _resolve_ch(ch, yto, cache)
                        d10 = abs(round(xf_ch - ych10, 2))
                        if (d10 > mn and d10 <= mx
                            and ((ych10 > xf_ch and ydir == 'DN') or (ych10 < xf_ch and ydir == 'UP'))):
                            _b10 = f'[Dist. from {yto} (CH-{round(abs(ych10),2)}) to {xf} (CH-{round(abs(xf_ch),2)}) is ({d10}M)]'
                            if cv == '':
                                d10l = _b10; cv = d10l; q10 = 1
                            elif (d2+d4+d6+d8) > 0 and d10 < _min_dist_val(d2l+d4l+d6l+d8l):
                                d10l = _b10
                                if d1l: cv = d1l + ' & ' + d10l
                                elif d3l: cv = d3l + ' & ' + d10l
                                elif d5l: cv = d5l + ' & ' + d10l
                                elif d7l: cv = d7l + ' & ' + d10l
                                elif d9l: cv = d9l + ' & ' + d10l
                                elif (valid := [v for v in (d2,d4,d6,d8) if v > 0]) and d10 < min(valid): cv = d10l
                                else: cv = d10l
                                q10 = 1
                        elif d10 < mn or d10 > mx:
                            if q1+q2+q3+q4+q5+q6+q7+q8+q9 == 0: cv = ''
                            xw10 = 1

                elif (d1+d2+d3+d4+d5+d6+d7+d8+d9+d10
                      + xw1+xw2+xw3+xw4+xw5+xw6+xw7+xw8+xw9+xw10 == 0):
                    cv = f'{sq_idx[i]} and {sq_col[j]} are Parallel/Isolated Movements'

        results.append((i, j, cv, ft_v, is_ft))
    return results


# ── Main entry point ──────────────────────────────────────────────────────

def _precompute_toc_data(toc: pd.DataFrame, extras: dict) -> dict:
    cols = ['TO', 'FROM', 'DIR', 'GN', 'OV-TC', 'OV-PT-N', 'OV-PT-R',
            'RT-PT-N', 'RT-PT-R', 'ISO', 'RT-TC', 'FROM-TO', 'TCK', 'UN']
    data: dict = {c: (toc[c].tolist() if c in toc.columns else [''] * len(toc)) for c in cols}
    data['_gn_set'] = set(data['GN'])
    data.update(extras)
    return data


def criss_cross_mvt(
    toc: pd.DataFrame,
    sqsh: pd.DataFrame,
    path_sqsh_xx: str,
    path_sqsh_cc: str,
    path_xx_pt_list: str,
    path_xx_mt_list: str,
    path_ch: str,
    cc_dist_min: float,
    cc_dist_max: float,
    parent=None,
    progress_cb=None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Compute criss-cross movements. Uses fork-Pool on Linux for speed."""
    from ..io.readers import read_chainage_file
    from ..io.writers import write_table_file

    cc_dist_min = float(cc_dist_min)
    cc_dist_max = float(cc_dist_max)
    toc = toc.copy()

    # ── String normalisation ─────────────────────────────────────────────
    for col in ['FROM', 'TO', 'GN', 'RT-TC', 'OV-TC',
                'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R',
                'ISO', 'ISO-TC', 'UN', 'OV-SET', 'DIR']:
        if col in toc.columns:
            toc[col] = toc[col].astype(str).replace('nan', '')

    rm_pat = r'[ \.\-\n#@\*\?\^\$\(\)]'
    nr_pat = '|'.join(NR_PATTERNS) if NR_PATTERNS else r'\bN\b|\bR\b'
    for col in ['FROM', 'TO', 'GN', 'RT-TC', 'OV-TC',
                'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R', 'ISO', 'UN']:
        if col in toc.columns:
            toc[col] = toc[col].str.replace(rm_pat, '', regex=True)
            if 'PT' in col or col == 'ISO':
                toc[col] = toc[col].str.replace(nr_pat, '', regex=True)

    if 'ISO-PT-N' in toc.columns:
        toc['RT-PT-N'] = _merge_iso_pts(
            toc['RT-PT-N'],
            toc['ISO-PT-N'].fillna('').astype(str).str.replace('nan', '', regex=False), '')
    if 'ISO-PT-R' in toc.columns:
        toc['RT-PT-R'] = _merge_iso_pts(
            toc['RT-PT-R'],
            toc['ISO-PT-R'].fillna('').astype(str).str.replace('nan', '', regex=False), '')

    # ── Chainage ─────────────────────────────────────────────────────────
    _ch_provided = bool(path_ch and os.path.isfile(str(path_ch).strip()))
    if _ch_provided:
        ch = read_chainage_file(path_ch)
        ch = ch.fillna('')
    else:
        ch = pd.DataFrame(columns=['GR_NO', 'GR_CH'])
    ch['GR_CH'] = pd.to_numeric(ch['GR_CH'], errors='coerce')  # blank -> NaN = missing
    ch['GR_NO'] = ch['GR_NO'].astype(str).str.strip()

    missing = _missing_chainage(toc, ch)
    if missing:
        logger.warning(
            "Criss-cross: no chainage for %d signal(s)/point(s); pairs that "
            "need them stay conflicting: %s", len(missing), ', '.join(missing))

    # ── Square sheet ─────────────────────────────────────────────────────
    # A one-sided conflict ('~') is still a conflict: never offer it for
    # criss-cross dispensation.
    sqsh = sqsh.copy().fillna('').replace('~', 'X', regex=False)
    sqsh_xx = sqsh.replace('X', '', regex=True)
    sqsh_cc = sqsh_xx.copy()

    toc = toc.fillna('')
    l = len(sqsh.index)
    total_pairs = l * (l + 1) // 2

    _empty = [''] * len(toc)
    _iso_pt_n = (toc['ISO-PT-N'].fillna('').astype(str).tolist()
                 if 'ISO-PT-N' in toc.columns else _empty)
    _iso_pt_r = (toc['ISO-PT-R'].fillna('').astype(str).tolist()
                 if 'ISO-PT-R' in toc.columns else _empty)
    _iso_tc   = (toc['ISO-TC'].fillna('').astype(str).tolist()
                 if 'ISO-TC' in toc.columns else _empty)

    toc = toc[[
        'FROM', 'TO', 'GN', 'RT-TC', 'OV-TC',
        'RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R',
        'FROM-TO', 'DIR', 'TCK', 'ISO',
    ]].replace(r'[~\n]', '', regex=True)

    for col in ['RT-PT-N', 'RT-PT-R', 'OV-PT-N', 'OV-PT-R', 'ISO']:
        toc[col] = toc[col].str.replace(r'[NR\n\\]', '', regex=True)

    toc_data = _precompute_toc_data(toc, {
        'ISO-PT-N': _iso_pt_n, 'ISO-PT-R': _iso_pt_r, 'ISO-TC': _iso_tc,
    })
    sqsh_index = list(sqsh_xx.index)
    sqsh_cols  = list(sqsh_xx.columns)
    n_rows = len(toc)

    # ── Populate global context (before any fork) ────────────────────────
    global _CRISS_CTX
    _CRISS_CTX = {
        'toc':        toc,
        'toc_data':   toc_data,
        'ch':         ch,
        'sqsh_vals':  sqsh.values.tolist(),
        'sqsh_index': sqsh_index,
        'sqsh_cols':  sqsh_cols,
        'cc_dist_min': cc_dist_min,
        'cc_dist_max': cc_dist_max,
        'l':          l,
    }

    logger.info("Criss-cross: %d rows, %d pairs", n_rows, total_pairs)

    # ── Loop 1: collision detection (parallel on Linux) ──────────────────
    use_mp = platform.system() == 'Linux' and n_rows >= 20
    done = 0

    def _apply(row_results):
        nonlocal done
        for ri, rj, xx_ij, ft_val, is_ft in row_results:
            if xx_ij:
                sqsh_xx.iloc[ri, rj] = xx_ij
            if is_ft and ft_val:
                sqsh_xx.iloc[rj, ri] = ft_val
            done += 1
            if progress_cb and total_pairs > 0 and done % max(1, total_pairs // 50) == 0:
                progress_cb(done / total_pairs * 100.0)

    if use_mp:
        n_workers = min(mp.cpu_count(), n_rows)
        chunk = max(1, n_rows // (n_workers * 4))
        logger.info("Using %d workers (chunksize=%d)", n_workers, chunk)
        mp_ctx = mp.get_context('fork')
        with mp_ctx.Pool(processes=n_workers) as pool:
            for rr in pool.imap_unordered(_criss_row_worker, range(n_rows), chunksize=chunk):
                _apply(rr)
    else:
        for i in range(n_rows):
            _apply(_criss_row_worker(i))

    _CRISS_CTX.clear()

    # ── Loop 2: build xx_pt_list / xx_mt_list ────────────────────────────
    toc['FROM_TO_SIG'] = toc['FROM'].astype(str) + '_' + toc['TO'].astype(str)
    fts = toc['FROM_TO_SIG'].tolist()

    mn_m = pd.Series(dtype='object'); sb_m = pd.Series(dtype='object')
    xx_p = pd.Series(dtype='object'); mn1  = pd.Series(dtype='object')
    sb1  = pd.Series(dtype='object'); xx_t = pd.Series(dtype='object')
    k = 0

    for i in range(l):
        for j in range(i, l):
            sx  = sqsh_xx.iloc[i, j]
            asx = str(sqsh_xx.index[i])
            bsx = str(sqsh_xx.columns[j])
            asx = (asx.split('_')[0] + '_0' if '_' in asx else asx + '_0')
            bsx = (bsx.split('_')[0] + '_0' if '_' in bsx else bsx + '_0')
            sq  = sqsh.iloc[i, j]
            ns  = toc_data['TO'][i]
            if (('from_to' in sx or 'Dist' not in sx or sq == 'X' or asx == bsx
                 or re.sub(r'[a-zA-Z]', '', asx.split('_')[0]) == re.sub(r'[a-zA-Z]', '', bsx.split('_')[0])
                 or re.sub(r'[a-zA-Z]', '', str(ns)) == re.sub(r'[a-zA-Z]', '', bsx.split('_')[0]))
                    and i != j):
                sqsh_cc.iloc[i, j] = 'X'; sqsh_cc.iloc[j, i] = 'X'
            elif i == j:
                sqsh_cc.iloc[j, i] = '#'
            else:
                sqsh_cc.iloc[i, j] = ''; sqsh_cc.iloc[j, i] = ''

        k += 1
        for s in (mn_m, sb_m, xx_p, mn1, sb1, xx_t):
            s.at[k] = ''

    k = 0
    for i in range(l):
        for j in range(i, l):
            sx = sqsh_xx.iloc[i, j]
            if len(str(sx).strip()) > 0 and i != j:
                mn = fts[i] + '\\' + sqsh_xx.index[i]
                sb = fts[j] + '\\' + sqsh_xx.columns[j]
                if mn[0:2] == 'SH':
                    pass
                elif 'Parallel' in sx[0:8] or 'Isolated' in sx[0:8] or 'from_to' in sx:
                    mn_m.at[k] = mn; sb_m.at[k] = sb; xx_p.at[k] = sx; k += 1
                else:
                    mn1.at[k] = mn; sb1.at[k] = sb; xx_t.at[k] = sx; k += 1
        k += 1
        for s in (mn_m, sb_m, xx_p, mn1, sb1, xx_t):
            s.at[k] = ''

    xx_pt_list = pd.DataFrame({'mn_mvt': mn_m, 'sb_mvt': sb_m, 'xx_pt': xx_p})
    xx_mt_list = pd.DataFrame({'mn_mvt': mn1,  'sb_mvt': sb1,  'xx_mt': xx_t})

    from ..io.writers import write_table_file
    write_table_file(sqsh_xx,    path_sqsh_xx,   index=True)
    write_table_file(sqsh_cc,    path_sqsh_cc,   index=True)
    write_table_file(xx_pt_list, path_xx_pt_list)
    write_table_file(xx_mt_list, path_xx_mt_list)

    if progress_cb:
        progress_cb(100.0)

    return sqsh_cc, sqsh_xx, xx_pt_list
