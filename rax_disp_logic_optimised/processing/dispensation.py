"""
Dispensation text generation (Section 3.47 & 5.16 movements).

Generates human-readable movement descriptions for:
  - Main movements (dispatched / received)
  - Sub-movements (simultaneous movements)
  - Shunting movements
"""

import re
import logging
import pandas as pd
from typing import List

logger = logging.getLogger(__name__)

_RE_UN = r"[a-zA-Z_]"


def disp_lit_fn(toc, disp, hs, end, l_no, sqsh_xx):
    """Generate SH-DISP-LIT, MN-MOVT-LIT, SUB-MOVT-LIT columns."""
    if disp == '':
        toc['NEW-DISP2'] = ''
        return toc

    toc = toc.copy()
    disp_series = toc[disp].fillna("").astype(str)
    sqsh_xx = sqsh_xx.copy().astype(str).fillna('')
    sqsh_xx.index   = sqsh_xx.index.astype(str) + '_0'
    sqsh_xx.columns = sqsh_xx.columns.astype(str) + '_0'

    il = len(toc.index)
    toc['FROM-TO'] = toc['FROM-TO'].astype(str) + '_0'

    for i in range(il):
        x1 = x2 = x3 = ""
        sh_mvt = ""

        mn_sig = pd.Series(toc.at[toc.index[i], 'FROM'])

        disp_raw = disp_series.iat[i]
        disp_clean = (
            disp_raw.replace(r'\s', '').replace(' ', '')
                    .replace(',', '').replace('\n', ',')
        )
        disp_parts = [x.strip() for x in disp_clean.split(',') if x.strip()]

        disp_sh, disp_mn = [], []
        for token in disp_parts:
            if token:
                if token[:2].upper() == 'SH':
                    disp_sh.append(token + '_0')
                else:
                    disp_mn.append(token + '_0')

        # ── 5.16 shunting movements ───────────────────────────────────────
        if disp_sh:
            disp_toc_sh = toc[toc['FROM-TO'].str.fullmatch(
                '|'.join(disp_sh), na=False)].copy()
            if not disp_toc_sh.empty:
                sh_text = (
                    disp_toc_sh['DIR'].values
                    + " shunting movement from shunt signal "
                    + disp_toc_sh['FROM'].values
                    + " to " + disp_toc_sh['TO'].values
                )
                _rpt_n = disp_toc_sh['RT-PT-N'].fillna('').astype(str)
                _rpt_r = disp_toc_sh['RT-PT-R'].fillna('').astype(str)
                if (_rpt_n != '').any() or (_rpt_r != '').any():
                    ss = 0
                    sh_text = sh_text + " with route point/s "
                    if (_rpt_n != '').any():
                        ss = 1
                        sh_text = sh_text + "(" + _rpt_n.values + ")"
                    if (_rpt_r != '').any():
                        if ss == 1:
                            sh_text = sh_text + " & (" + _rpt_r.values + ")"
                        else:
                            sh_text = sh_text + " (" + _rpt_r.values + ")"
                sh_text = sh_text + " and vice-versa"
                disp_toc_sh['FROM-TO-SIG'] = sh_text
                disp_sh_mvt     = disp_toc_sh['FROM-TO-SIG'].values
                disp_sh_mvt_frmto = disp_toc_sh['FROM-TO'].values
                sqsh_sh_i = toc.at[toc.index[i], 'FROM-TO']
                sh_lines = []
                for k, sh in enumerate(disp_sh_mvt):
                    sqsh_sh_j = disp_sh_mvt_frmto[k]
                    cond = str(sqsh_xx.loc[sqsh_sh_i, sqsh_sh_j]).strip()
                    sh_lines.append(sh + (" (" + cond + ")" if cond else ""))
                sh_mvt = "\n".join(sh_lines)

        if not mn_sig.str.contains('H', na=False).any():
            # ── 3.47 main movements ───────────────────────────────────────
            if disp_mn:
                disp_mn = [x.strip() for x in disp_mn if x.strip()]
                disp_mn_toc = toc[toc['FROM-TO'].isin(set(disp_mn))].copy()
                sub_movts = []
                disp_idx = disp_mn_toc.index

                for k in range(len(disp_idx)):
                    row_idx = disp_idx[k]
                    sqsh_j = disp_mn_toc.at[row_idx, 'FROM-TO']
                    sqsh_i = toc.at[toc.index[i], 'FROM-TO']
                    dir_k  = disp_mn_toc.at[row_idx, 'DIR']
                    frto_k = disp_mn_toc.iloc[k, disp_mn_toc.columns.get_loc('FRTO')]
                    end_k  = disp_mn_toc.at[row_idx, 'END']
                    from_k = disp_mn_toc.at[row_idx, 'FROM']
                    to_k   = disp_mn_toc.iloc[k, disp_mn_toc.columns.get_loc('TO')]
                    rd_k   = disp_mn_toc.at[row_idx, 'RD']

                    if rd_k == 'received':
                        x2 = (dir_k + " train " + frto_k + " " + end_k
                              + " end may be " + rd_k
                              + " by taking off " + from_k)
                        x2 = (x2 + disp_re_line(
                                re.sub(_RE_UN, '', disp_mn_toc.at[row_idx, 'UN']), l_no)
                              + " up to " + to_k)
                    else:
                        x2 = (dir_k + " train may be " + rd_k
                              + disp_line(from_k, toc, l_no)
                              + " by taking off " + from_k
                              + " up to " + to_k)

                    ss1 = ss2 = ss3 = 0
                    if (len(disp_mn_toc.at[row_idx, 'RT-PT-N']) > 1
                            or len(disp_mn_toc.at[row_idx, 'RT-PT-R']) > 1):
                        x2 = x2 + " with route point/s"
                        if len(disp_mn_toc.at[row_idx, 'RT-PT-N']) > 1:
                            ss1 = 1
                            x2 = x2 + " (" + disp_mn_toc.at[row_idx, 'RT-PT-N'] + ")"
                        if len(disp_mn_toc.at[row_idx, 'RT-PT-R']) > 1:
                            x2 = x2 + (" & (" if ss1 else " (") + disp_mn_toc.at[row_idx, 'RT-PT-R'] + ")"
                    if "OV" in disp_mn_toc.at[row_idx, 'OV-SET']:
                        x2 = x2 + " with overlap (" + disp_mn_toc.at[row_idx, 'OV-SET'] + ")"
                        if (len(disp_mn_toc.at[row_idx, 'OV-PT-N']) > 1
                                or len(disp_mn_toc.at[row_idx, 'OV-PT-R']) > 1):
                            x2 = x2 + " point/s"
                            if len(disp_mn_toc.at[row_idx, 'OV-PT-N']) > 1:
                                ss2 = 1
                                x2 = x2 + " (" + disp_mn_toc.at[row_idx, 'OV-PT-N'] + ")"
                            if len(disp_mn_toc.at[row_idx, 'OV-PT-R']) > 1:
                                x2 = x2 + (" & (" if ss2 else " (") + disp_mn_toc.at[row_idx, 'OV-PT-R'] + ")"
                    iso_len = len(disp_mn_toc.at[row_idx, 'ISO'])
                    iso_tc  = disp_iso_tc_fn(disp_mn_toc, 'ISO-TC', k)
                    if iso_len > 1 or len(iso_tc) > 1:
                        x2 = x2 + " with following isolation"
                    if iso_len > 1:
                        ss3 = 1
                        x2 = x2 + " point/s (" + disp_mn_toc.at[row_idx, 'ISO'] + ")"
                    if len(iso_tc) > 1:
                        x2 = x2 + (" & track/s (" if ss3 else " track/s (") + iso_tc + ")"
                    if rd_k == 'received':
                        x2 = x2 + " and vice versa."
                    x2 = x2 + sqsh_xx.loc[sqsh_i, sqsh_j]
                    sub_movts.append(x2)

                x2 = "\n".join(sub_movts)

                # ── Main movement text (x1) ───────────────────────────────
                rd_main = toc.at[toc.index[i], 'RD']
                if rd_main == 'received':
                    x1 = ("When a " + toc.at[toc.index[i], 'DIR'] + " train "
                          + toc.iloc[i, toc.columns.get_loc('FRTO')] + " "
                          + toc.at[toc.index[i], 'END']
                          + " end is being " + rd_main
                          + " by taking off " + toc.at[toc.index[i], 'FROM'])
                    x1 = (x1 + disp_re_line(
                            re.sub(_RE_UN, '', toc.at[toc.index[i], 'UN']), l_no)
                          + " up to " + toc.iloc[i, toc.columns.get_loc('TO')])
                else:
                    x1 = (" When a " + toc.at[toc.index[i], 'DIR'] + " train is being "
                          + rd_main
                          + disp_line(toc.iloc[i, toc.columns.get_loc('FROM')], toc, l_no)
                          + " by taking off " + toc.iloc[i, toc.columns.get_loc('FROM')]
                          + " up to " + toc.at[toc.index[i], 'TO'])

                ss1 = ss2 = ss3 = 0
                if (len(toc.at[toc.index[i], 'RT-PT-N']) > 1
                        or len(toc.at[toc.index[i], 'RT-PT-R']) > 1):
                    x1 = x1 + " with route point/s"
                    if len(toc.at[toc.index[i], 'RT-PT-N']) > 1:
                        ss1 = 1
                        x1 = x1 + " (" + toc.at[toc.index[i], 'RT-PT-N'] + ")"
                    if len(toc.at[toc.index[i], 'RT-PT-R']) > 1:
                        x1 = x1 + (" & (" if ss1 else " (") + toc.at[toc.index[i], 'RT-PT-R'] + ")"
                if "OV" in toc.at[toc.index[i], 'OV-SET']:
                    x1 = x1 + " with overlap (" + toc.at[toc.index[i], 'OV-SET'] + ")"
                    if (len(toc.at[toc.index[i], 'OV-PT-N']) > 1
                            or len(toc.at[toc.index[i], 'OV-PT-R']) > 1):
                        x1 = x1 + " point/s"
                        if len(toc.at[toc.index[i], 'OV-PT-N']) > 1:
                            ss2 = 1
                            x1 = x1 + " (" + toc.at[toc.index[i], 'OV-PT-N'] + ")"
                        if len(toc.at[toc.index[i], 'OV-PT-R']) > 1:
                            x1 = x1 + (" & (" if ss2 else " (") + toc.at[toc.index[i], 'OV-PT-R'] + ")"
                iso_len_main = len(toc.at[toc.index[i], 'ISO'])
                iso_tc_main  = disp_iso_tc_fn(toc, 'ISO-TC', i)
                if iso_len_main > 1 or len(iso_tc_main) > 1:
                    x1 = x1 + " and with isolation"
                if iso_len_main > 1:
                    ss3 = 1
                    x1 = x1 + " point/s (" + toc.at[toc.index[i], 'ISO'] + ")"
                if len(iso_tc_main) > 1:
                    x1 = x1 + (", track/s (" if ss3 else " track/s (") + iso_tc_main + ")"
                x1 = x1 + ", the following simultaneous movements may be allowed::-\n"

            # ── 5.16 shunt text (x3) ─────────────────────────────────────
            if disp_sh:
                rd_main = toc.at[toc.index[i], 'RD']
                if rd_main == 'received':
                    x3 = ("When a " + toc.at[toc.index[i], 'DIR'] + " train "
                          + toc.iloc[i, toc.columns.get_loc('FRTO')] + " "
                          + toc.at[toc.index[i], 'END']
                          + " end is being " + rd_main
                          + " by taking off " + toc.at[toc.index[i], 'FROM'])
                    x3 = (x3 + disp_re_line(
                            re.sub(_RE_UN, '', toc.at[toc.index[i], 'UN']), l_no)
                          + " up to " + toc.iloc[i, toc.columns.get_loc('TO')])
                else:
                    x3 = ("When a " + toc.at[toc.index[i], 'DIR'] + " train is being "
                          + rd_main
                          + disp_line(toc.at[toc.index[i], 'FROM'], toc, l_no)
                          + " by taking off " + toc.at[toc.index[i], 'FROM']
                          + " up to " + toc.at[toc.index[i], 'TO'])

                ss1 = ss2 = ss3 = 0
                if (len(toc.at[toc.index[i], 'RT-PT-N']) > 1
                        or len(toc.at[toc.index[i], 'RT-PT-R']) > 1):
                    x3 = x3 + " with route point/s"
                    if len(toc.at[toc.index[i], 'RT-PT-N']) > 1:
                        ss1 = 1
                        x3 = x3 + " (" + toc.at[toc.index[i], 'RT-PT-N'] + ")"
                    if len(toc.at[toc.index[i], 'RT-PT-R']) > 1:
                        x3 = x3 + (" & (" if ss1 else " (") + toc.at[toc.index[i], 'RT-PT-R'] + ")"
                if "OV" in toc.at[toc.index[i], 'OV-SET']:
                    x3 = x3 + " with overlap (" + toc.at[toc.index[i], 'OV-SET'] + ")"
                    if (len(toc.at[toc.index[i], 'OV-PT-N']) > 1
                            or len(toc.at[toc.index[i], 'OV-PT-R']) > 1):
                        x3 = x3 + " point/s"
                        if len(toc.at[toc.index[i], 'OV-PT-N']) > 1:
                            ss2 = 1
                            x3 = x3 + " (" + toc.at[toc.index[i], 'OV-PT-N'] + ")"
                        if len(toc.at[toc.index[i], 'OV-PT-R']) > 1:
                            x3 = x3 + (" & (" if ss2 else " (") + toc.at[toc.index[i], 'OV-PT-R'] + ")"
                iso_len_main = len(toc.at[toc.index[i], 'ISO'])
                iso_tc_main  = disp_iso_tc_fn(toc, 'ISO-TC', i)
                if iso_len_main > 1 or len(iso_tc_main) > 1:
                    x3 = x3 + " with following isolation"
                    if iso_len_main > 1:
                        ss3 = 1
                        x3 = x3 + " point/s (" + toc.at[toc.index[i], 'ISO'] + ")"
                    if len(iso_tc_main) > 1:
                        x3 = x3 + (", track/s (" if ss3 else " track/s (") + iso_tc_main + ")"
                x3 = x3 + ", the following shunting movements may be permitted ::-\n"
                x3 = x3 + sh_mvt + "."

        toc.at[toc.index[i], 'SH-DISP-LIT'] = x3
        toc.at[toc.index[i], 'MN-MOVT-LIT'] = x1
        toc.at[toc.index[i], 'SUB-MOVT-LIT'] = x2

    return toc


def disp_line(frm: str, toc: pd.DataFrame, l_no: str) -> str:
    """Generate 'from line No.- N' text for dispatched movements."""
    if frm.startswith('SH'):
        return ""

    l_toc = toc.loc[toc['TO'] == frm, 'UN'].astype(str).values
    l_toc = [u for u in l_toc if u and u != '']

    if not l_toc:
        return ""

    digits = re.sub(r'\D', '', l_toc[0])
    if not digits:
        return ""

    try:
        line_num = int(digits)
        ref_line = int(l_no)
        return f" from line No.- {line_num}" if line_num <= ref_line else ""
    except ValueError:
        return ""


def disp_re_line(un: str, l_no: str) -> str:
    """Generate 'for line No.- N' text for received movements."""
    if not un:
        return ""

    l = re.sub(r'\D', '', str(un))
    if not l:
        return ""

    try:
        l = int(l)
        l_no = int(l_no)
        return f" for line No.- {l}" if l <= l_no else ""
    except ValueError:
        return ""


def disp_iso_tc_fn(toc: pd.DataFrame, iso_cl: str, i: int) -> str:
    """Safely retrieve ISO-TC value for row i."""
    try:
        tc = toc.iloc[i, toc.columns.get_loc(iso_cl)]
        return str(tc) if len(tc) > 0 else ''
    except (IndexError, KeyError, TypeError):
        return ''


def is_integer(n) -> bool:
    """Check if value can be converted to int."""
    try:
        int(n)
        return True
    except (ValueError, TypeError):
        return False


def min_dist(text: str) -> float:
    """Extract minimum distance from text containing 'is (N.NNM)'."""
    pattern = r'is \(([\d\.]+)M\)'
    distances = [float(d) for d in re.findall(pattern, str(text))]
    return min(distances) if distances else 0.0


def safe_label_prefix(value) -> str:
    """Return the prefix before '_' for any value."""
    s = str(value)
    return s.split('_')[0] if '_' in s else s


def rm_dup(li: list) -> list:
    """Remove duplicates from a list, clean parentheses, filter by length."""
    if not isinstance(li, list):
        return []

    rm_chars = ['(', ')']
    cleaned = []
    for x in li:
        x = x.strip()
        for ch in rm_chars:
            x = x.replace(ch, '')
        x = x.strip()
        if 3 <= len(x) <= 7:
            cleaned.append(x)

    seen = set()
    deduped = []
    for x in cleaned:
        if x not in seen:
            seen.add(x)
            deduped.append(x)

    try:
        deduped.sort()
    except TypeError:
        pass

    return deduped
