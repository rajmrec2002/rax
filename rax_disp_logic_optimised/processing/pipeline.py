"""
End-to-end "Run Fn" pipeline: TOC -> locking -> square sheet ->
(criss-cross per distance band) -> dispensation documents.

Used by the GUI and by headless/batch runs, so both produce identical
results.
"""

import logging
import math
import os
import re
from dataclasses import dataclass, field
from time import strftime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from ..io.exporters import write_disp347_xlsx, write_disp516_xlsx
from ..io.preprocessor import detect_toc_format, read_formatted_toc, read_toc_file
from ..io.writers import write_table_file
from .criss_cross import criss_cross_mvt, load_chainage, missing_chainage
from .dispensation import disp_lit_fn
from .isolation import apply_added_iso, find_isolation, isolated_pairs_sheet
from .locking import ixl_fn
from .square_sheet import (
    new_disp_gen_frm_sqsh, new_lck_gen_frm_sqsh, re_vice_versa, sqsh_fn, vice_versa,
)
from .toc_checks import permitted_pair_checks, toc_data_checks
from .toc_format import toc_format

logger = logging.getLogger(__name__)

Band = Tuple[float, float]


def parse_bands(text: str) -> List[Band]:
    """
    Parse distance bands in metres.

    '0-120, 120-300, 300+'  -> [(0, 120), (120, 300), (300, inf)]
    Also accepted for an open upper end: '300-', '>300'.
    Each band means  min < distance <= max  (as in criss-cross).
    """
    bands: List[Band] = []
    for part in re.split(r'[,;\n]+', str(text or '')):
        p = part.strip().lower().replace('m', '').replace(' ', '')
        if not p:
            continue
        m = re.fullmatch(r'(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)', p)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
        else:
            m = re.fullmatch(r'(?:>)?(\d+(?:\.\d+)?)(?:\+|-|)', p)
            if not m or not (p.endswith(('+', '-')) or p.startswith('>')):
                raise ValueError(f'Cannot read distance band "{part.strip()}" '
                                 '(use e.g. 0-120, 120-300, 300+)')
            lo, hi = float(m.group(1)), math.inf
        if hi <= lo:
            raise ValueError(f'Distance band "{part.strip()}": maximum must be above minimum')
        bands.append((lo, hi))
    return bands


def band_label(band: Band) -> str:
    lo, hi = band
    f = lambda v: f'{v:g}'
    return f'{f(lo)}m+' if math.isinf(hi) else f'{f(lo)}-{f(hi)}m'


def parse_home_signals(hs_texts: Sequence[str], end_texts: Sequence[str]):
    """GUI text fields -> (hs, end) lists as used by toc_format()."""
    hs, end = [], []
    for hs_text, end_text in zip(hs_texts, end_texts):
        hs_text, end_text = (hs_text or '').strip(), (end_text or '').strip()
        if hs_text and not end_text:      # group without an end name is ignored
            hs.append(['']); end.append('')
        else:
            hs.append([x.strip().upper() + '_' for x in hs_text.split(',') if x.strip()] or [''])
            end.append(end_text.upper())
    return hs, end


@dataclass
class RunResult:
    paths: Dict[str, str] = field(default_factory=dict)
    checks: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Signals / points with no chainage (criss-cross runs only).  Pairs
    # that need one of them stay locked.
    missing_chainage: List[str] = field(default_factory=list)
    # Isolation (ISO preferred over dispensation): counts by result
    isolation: Dict[str, int] = field(default_factory=dict)


def _read_toc(toc_path: str, hs, end, l_no: int) -> pd.DataFrame:
    toc = read_formatted_toc(toc_path) if detect_toc_format(toc_path) else read_toc_file(toc_path)
    return toc_format(toc, hs, end, l_no)


def read_toc_preview(toc_path: str) -> pd.DataFrame:
    """Raw TOC columns (for home-signal auto-detect), raw or formatted file."""
    from ..core.constants import TOC_BLANK_COLS, TOC_INPUT_COLS
    from ..io.readers import read_table_file
    if not detect_toc_format(toc_path):
        return read_toc_file(toc_path)
    df = read_table_file(toc_path)
    for col in TOC_INPUT_COLS + TOC_BLANK_COLS:
        if col not in df.columns:
            df[col] = ''
    return df[TOC_INPUT_COLS + TOC_BLANK_COLS].fillna('').reset_index(drop=True)


def check_chainage(toc_path: str, chainage_path: str = '',
                   extra: Optional[Dict[str, float]] = None) -> List[str]:
    """
    Signals and points of the TOC that have no chainage in `chainage_path`
    or `extra`.  Run before a criss-cross run so the user can supply them.
    """
    hs, end = parse_home_signals([''] * 4, [''] * 4)
    toc = _read_toc(toc_path, hs, end, 1)
    have = set((extra or {}).keys())
    return [k for k in missing_chainage(toc, chainage_path) if k not in have]


def parse_chainage_text(text: str) -> Dict[str, float]:
    """
    'S12, 1234.5\nPT101 = 1300' -> {'S12': 1234.5, 'PT101': 1300.0}

    One entry per line, name and chainage separated by ',', '=', ':' or
    whitespace.  Lines with a blank chainage are skipped.
    """
    out: Dict[str, float] = {}
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [x for x in re.split(r'[,=:;\t ]+', line) if x]
        if len(parts) < 2:
            continue
        try:
            out[parts[0].upper()] = float(parts[1])
        except ValueError:
            raise ValueError(f'Chainage "{line}": "{parts[1]}" is not a number')
    return out


def _chainage_with_extra(chainage_path: str, extra: Dict[str, float], out_path: str) -> str:
    """Write the chainage file plus the extra entries (extra ones win) to out_path."""
    ch = load_chainage(chainage_path)[['GR_NO', 'GR_CH']]
    add = pd.DataFrame(sorted(extra.items()), columns=['GR_NO', 'GR_CH'])
    merged = pd.concat([ch[~ch['GR_NO'].isin(add['GR_NO'])], add], ignore_index=True)
    merged.to_csv(out_path, index=False)
    return out_path


def _conflicts(sqsh: pd.DataFrame) -> set:
    m = sqsh.fillna('').astype(str).isin(['X', '~'])
    idx = list(sqsh.index)
    return {frozenset((idx[i], sqsh.columns[j])) for i, j in zip(*m.values.nonzero()) if i != j}


def _write_iso_report(path: str, iso, ltoc: pd.DataFrame, sqsh_iso: pd.DataFrame) -> None:
    """Isolation workbook: summary, pairs, per movement, layout check, square sheet."""
    summary = pd.DataFrame([
        ('Pairs isolated (no dispensation needed)', iso.summary.get('isolated', 0)),
        ('  isolation points added to ISO', iso.summary.get('iso points added', 0)),
        ('  conditional isolation entries', iso.summary.get('conditional entries', 0)),
        ('Pairs that cannot be isolated (keep dispensation)', iso.summary.get('not isolable', 0)),
        ('Pairs to check: track layout from TOC doubtful (keep dispensation)',
         iso.summary.get('check layout', 0)),
        ('Pairs no single overshoot can bring together (double overshoot ignored)',
         iso.summary.get('separated', 0)),
        ('', ''),
        ('Rule', 'One train overshoots at a time. A movement with an overlap stays within '
                 'it; one without (calling-on, shunt) runs on over unlocked points: facing '
                 'points send it the way they lie, trailing points let it through. Isolation '
                 'is needed where that reaches the other movement\'s route; reaching only its '
                 'overlap would need both to overshoot (double overshoot) and is ignored.'),
        ('COND-ISO format', '#<point><N/R># with <movements> set = point locked in that position '
                            'only while this movement and one of those movements are both set'),
    ], columns=['ITEM', 'VALUE'])
    by_movt = ltoc[['FROM-TO', 'ISO', 'ISO-ADDED', 'COND-ISO']]
    by_movt = by_movt[(by_movt['ISO-ADDED'] != '') | (by_movt['COND-ISO'] != '')]
    with pd.ExcelWriter(path, engine='openpyxl') as xw:
        summary.to_excel(xw, sheet_name='SUMMARY', index=False)
        iso.report.to_excel(xw, sheet_name='PAIRS', index=False)
        by_movt.to_excel(xw, sheet_name='BY MOVEMENT', index=False)
        iso.checks.to_excel(xw, sheet_name='LAYOUT CHECK', index=False)
        sqsh_iso.to_excel(xw, sheet_name='SQSH (I = isolated)')


def run_dispensation(
    toc_path: str,
    save_dir: str,
    stn: str,
    hs, end,
    l_no: int,
    *,
    vv_file: str = '',
    criss_cross: bool = False,
    chainage_path: str = '',
    bands: Optional[Sequence[Band]] = None,
    extra_chainage: Optional[Dict[str, float]] = None,
    iso_preferred: bool = True,
    iso_distance: Optional[float] = 120.0,
    progress: Optional[Callable[[float], None]] = None,
    t_str: Optional[str] = None,
) -> RunResult:
    """
    Run the full pipeline and write the output files.

    Without criss-cross one set of documents is written.  With criss-cross
    one set is written per distance band; in a band only pairs whose
    criss-cross distance lies in (min, max] are dispensed.

    `extra_chainage` ({'S12': 1234.5, 'PT101': 1300}) adds to / overrides the
    chainage file; the chainage actually used is written as CH-USED.
    Signals and points still without chainage are listed in TOC-CHECK and
    written to a CH-MISSING template (fill in GR_CH and re-run).

    `iso_preferred`: for every pair of movements the locking allows together,
    look for isolation points outside both routes and overlaps (see
    processing/isolation.py).  Isolated pairs are left out of the 3.47 /
    5.16 dispensation; points no other movement needs the other way are
    added to ISO, the rest become conditional isolation (COND-ISO column).
    `iso_distance`: required minimum criss-cross distance (m) from the next
    stop signal to the point where an overshoot meets another route
    (measured with the chainage file; None = no limit).
    Results: STN_<stn>ISO-gen file.
    """
    prog = progress or (lambda _p: None)
    t_str = t_str or strftime('%Y%m%d_%H-%M')
    res = RunResult()
    p = lambda tag, ext='.xlsx': os.path.join(save_dir, f'STN_{stn}{tag}-gen-_{t_str}{ext}')

    prog(0)
    toc = _read_toc(toc_path, hs, end, l_no)
    checks = [toc_data_checks(toc).assign(SET='TOC data')]

    if criss_cross:
        if extra_chainage:
            res.paths['CH-USED'] = _chainage_with_extra(
                chainage_path, extra_chainage, p('CH-USED', '.csv'))
            chainage_path = res.paths['CH-USED']
        res.missing_chainage = missing_chainage(toc, chainage_path)
        if res.missing_chainage:
            why = ('No chainage file attached' if not (chainage_path and os.path.isfile(chainage_path))
                   else 'Not in chainage file')
            checks.append(pd.DataFrame({
                'ROUTE': res.missing_chainage,
                'CHECK': 'No chainage (criss-cross pairs that need it stay locked)',
                'DETAIL': why + '; fill in the CH-MISSING file and run again',
                'SET': 'chainage'}))
            res.paths['CH-MISSING'] = p('CH-MISSING', '.csv')
            pd.DataFrame({'GR_NO': res.missing_chainage, 'GR_CH': ''}).to_csv(
                res.paths['CH-MISSING'], index=False)
    prog(10)

    res.paths['SQSH'] = p('SQSH')
    if vv_file:
        ltoc = toc
        vvlsqsh = re_vice_versa(vv_file)
        vvlsqsh.fillna('', inplace=True)
        write_table_file(vvlsqsh, res.paths['SQSH'], index=True)
    else:
        ltoc = ixl_fn(toc, hs, end, l_no)
        vvlsqsh = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
        write_table_file(vvlsqsh, res.paths['SQSH'], index=True)
    prog(15)

    isolated = set()
    if iso_preferred:
        base = ixl_fn(toc, hs, end, l_no) if vv_file else ltoc
        iso = find_isolation(base, vvlsqsh, chainage_path=chainage_path,
                             distance=iso_distance)
        if not vv_file and (iso.toc['ISO-ADDED'] != '').any():
            # lock the added isolation points like any other ISO entry
            before = _conflicts(vvlsqsh)
            ltoc = ixl_fn(apply_added_iso(toc, iso), hs, end, l_no)
            vvlsqsh = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
            write_table_file(vvlsqsh, res.paths['SQSH'], index=True)
            extra = _conflicts(vvlsqsh) - before
            if extra:
                logger.warning('Isolation: %d pair(s) newly locked by added ISO points', len(extra))
        cols = iso.toc.set_index('FROM-TO')[['ISO-ADDED', 'COND-ISO']]
        ltoc = ltoc.join(cols, on='FROM-TO')
        ltoc[['ISO-ADDED', 'COND-ISO']] = ltoc[['ISO-ADDED', 'COND-ISO']].fillna('')
        isolated = iso.isolated
        res.isolation = iso.summary
        checks.append(iso.checks.assign(SET='layout (isolation)'))
        res.paths['ISO'] = p('ISO')
        _write_iso_report(res.paths['ISO'], iso, ltoc, isolated_pairs_sheet(vvlsqsh, isolated))
    prog(20)

    def _documents(sqsh, sqsh_xx, suffix):
        l_toc = new_lck_gen_frm_sqsh(ltoc.copy(), sqsh, 'NEW-LCK2')
        # isolated pairs run together under isolation: no dispensation
        dltoc = new_disp_gen_frm_sqsh(l_toc, isolated_pairs_sheet(sqsh, isolated, 'X')
                                      if isolated else sqsh, 'NEW-DSP2')
        res.paths['TOC' + suffix] = p('TOC' + suffix)
        write_table_file(dltoc, res.paths['TOC' + suffix], index=False)
        checks.append(permitted_pair_checks(dltoc).assign(
            SET='permitted pairs ' + (suffix.strip('_') or '(no criss-cross)')))
        dmvt = disp_lit_fn(dltoc, 'NEW-DSP2', hs, end, l_no, sqsh_xx)
        res.paths['347' + suffix] = p('347' + suffix)
        res.paths['516' + suffix] = p('516' + suffix)
        write_disp347_xlsx(dmvt, res.paths['347' + suffix], stn)
        write_disp516_xlsx(dmvt, res.paths['516' + suffix], stn)

    if not criss_cross:
        _documents(vvlsqsh, vvlsqsh.replace('X', '', regex=True), '')
    else:
        bands = list(bands or [])
        if not bands:
            raise ValueError('Criss-cross needs at least one distance band')
        span = 70.0 / len(bands)
        for k, band in enumerate(bands):
            sfx = '_CC' + band_label(band)
            start = 20 + k * span
            sqsh_cc, sqsh_xx, _ = criss_cross_mvt(
                toc, vvlsqsh,
                p('SQSH_XX' + sfx), p('SQSH_CC' + sfx),
                p('XX_PT_LIST' + sfx, '.csv'), p('XX_MT_LIST' + sfx, '.csv'),
                chainage_path, band[0], band[1],
                parent=None,
                progress_cb=lambda pct, s=start: prog(s + pct / 100.0 * span * 0.8),
            )
            res.paths['SQSH_CC' + sfx] = p('SQSH_CC' + sfx)
            _documents(sqsh_cc, sqsh_xx, sfx)
            prog(start + span)

    res.checks = pd.concat(checks, ignore_index=True)
    res.paths['TOC-CHECK'] = p('TOC-CHECK')
    out = res.checks if not res.checks.empty else pd.DataFrame(
        [('', 'No findings', '')], columns=['ROUTE', 'CHECK', 'DETAIL'])
    write_table_file(out, res.paths['TOC-CHECK'], index=False)
    if not res.checks.empty:
        logger.warning('TOC check: %d finding(s), see %s', len(res.checks), res.paths['TOC-CHECK'])
    prog(100)
    return res
