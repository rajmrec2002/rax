"""Distance bands, TOC data checks and the end-to-end pipeline."""
import math

import pandas as pd
import pytest

from helpers import make_toc
from rax_disp_logic_optimised.processing.pipeline import (
    band_label, parse_bands, parse_home_signals, run_dispensation,
)
from rax_disp_logic_optimised.processing.toc_checks import (
    permitted_pair_checks, toc_data_checks,
)
from rax_disp_logic_optimised.processing.toc_format import toc_format

NO_HS = [[''], [''], [''], ['']]
NO_END = ['', '', '', '']


def test_parse_bands():
    assert parse_bands('0-120, 120-300, 300+') == [(0, 120), (120, 300), (300, math.inf)]
    assert parse_bands('0-120m; >300') == [(0, 120), (300, math.inf)]
    assert parse_bands('300-') == [(300, math.inf)]
    assert parse_bands('') == []
    for bad in ['abc', '120-0', '300']:
        with pytest.raises(ValueError):
            parse_bands(bad)
    assert [band_label(b) for b in parse_bands('0-120,120-300,300+')] == ['0-120m', '120-300m', '300m+']


def test_parse_home_signals_ignores_group_without_end_name():
    hs, end = parse_home_signals(['S3, CO3', 'S38', '', ''], ['alpha', '', '', ''])
    assert hs == [['S3_', 'CO3_'], [''], [''], ['']]
    assert end == ['ALPHA', '', '', '']


def _fmt(rows):
    return toc_format(make_toc(rows), NO_HS, NO_END, 18)


def test_toc_checks_find_missing_track_circuits():
    toc = _fmt([
        dict(FROM='S56', TO='S46', UN='56', **{'RT-PT-N': '159/160N', 'RT-TC': '160T, 56T',
                                              'OV-PT-N': '145/146N'}),           # overlap w/o OV-TC
        dict(FROM='SH20', TO='S16', UN='JKCL1D', **{'RT-PT-N': '113/114N, 119/120N',
                                                   'RT-TC': '119T'}),            # 113/114 w/o TC
        dict(FROM='S3', TO='S21', UN='1', **{'RT-PT-N': '101/102N', 'RT-TC': '101T'}),   # ok
        dict(FROM='CO9', TO='S99', UN='5', **{'RT-PT-N': '301/302N'}),           # no S9 route
    ])
    c = toc_data_checks(toc).set_index('ROUTE')
    assert 'overlap' in c.at['S56_56', 'CHECK'].lower()
    assert c.at['SH20_JKCL1D', 'DETAIL'].startswith('113/114')
    assert 'Calling-on' in c.at['CO9_5', 'CHECK']
    assert 'S3_1' not in c.index


def test_permitted_head_on_pair_is_flagged():
    toc = pd.DataFrame({'FROM-TO': ['SH43_45', 'SH52_WCSL2'], 'FROM': ['SH43', 'SH52'],
                        'TO': ['S49', 'SH43'], 'DIR': ['DN', 'UP'],
                        'NEW-DSP2': ['\nSH52_WCSL2', '']})
    c = permitted_pair_checks(toc)
    assert len(c) == 1 and c.at[0, 'ROUTE'] == 'SH43_45 / SH52_WCSL2'


ROWS = [
    dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-TC': '3T, 101T'}),
    dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '105/106N', 'RT-TC': '105T, 12T'}),
    dict(FROM='S14', TO='S4', UN='14', **{'RT-PT-N': '107/108N', 'RT-TC': '107T, 14T'}),
]


def _write_toc(tmp_path):
    path = tmp_path / 'toc.xlsx'
    make_toc(ROWS).to_excel(path, index=False)
    return str(path)


def test_pipeline_without_criss_cross_writes_one_set(tmp_path):
    res = run_dispensation(_write_toc(tmp_path), str(tmp_path), 'TST', NO_HS, NO_END, 18,
                           t_str='T')
    assert {'SQSH', 'TOC', '347', '516', 'TOC-CHECK'} <= set(res.paths)
    for p in res.paths.values():
        assert (tmp_path / p.split('/')[-1]).exists(), p
    toc = pd.read_excel(res.paths['TOC'], dtype=str).fillna('')
    assert 'S12_12' in toc.at[0, 'NEW-DSP2']


def test_pipeline_writes_one_document_set_per_distance_band(tmp_path):
    ch = tmp_path / 'ch.xlsx'
    pd.DataFrame({'GR_NO': ['S2', 'S4', 'S12', 'S14', 'S21', 'S3'],
                  'GR_CH': [0.0, 50.0, 100.0, 400.0, 900.0, 1000.0]}).to_excel(ch, index=False)
    bands = parse_bands('0-120, 120-300, 300+')
    res = run_dispensation(_write_toc(tmp_path), str(tmp_path), 'TST', NO_HS, NO_END, 18,
                           criss_cross=True, chainage_path=str(ch), bands=bands, t_str='T')
    for lbl in ['0-120m', '120-300m', '300m+']:
        for kind in ['TOC', '347', '516', 'SQSH_CC']:
            key = f'{kind}_CC{lbl}'
            assert key in res.paths, key
            assert (tmp_path / res.paths[key].split('/')[-1]).exists()
    assert pd.read_excel(ch)['GR_CH'].tolist() == [0.0, 50.0, 100.0, 400.0, 900.0, 1000.0]
