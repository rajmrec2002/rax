"""Chainage handling in criss-cross."""
import math

import pandas as pd

from rax_disp_logic_optimised.processing.criss_cross import (
    _get_pt_ch_val, _missing_chainage, _resolve_ch,
)

CH = pd.DataFrame({'GR_NO': ['S21', 'PT117', 'S39', 'S40'], 'GR_CH': [1215.55, 819.8, 0.0, float('nan')]})


def test_missing_chainage_is_nan_not_zero():
    assert _resolve_ch(CH, 'S21', {}) == 1215.55
    assert _resolve_ch(CH, 'CO21', {}) == 1215.55      # falls back to S<n>
    assert _resolve_ch(CH, 'S39', {}) == 0.0            # a real 0.0 is kept
    assert math.isnan(_resolve_ch(CH, 'S3', {}))        # not in file
    assert math.isnan(_resolve_ch(CH, 'S40', {}))       # blank in file
    v, found = _get_pt_ch_val(CH, 'PT999', {})
    assert math.isnan(v) and not found
    assert _get_pt_ch_val(CH, 'PT117', {}) == (819.8, True)


def test_missing_distance_never_passes_range_check():
    d = abs(round(_resolve_ch(CH, 'S3', {}) - 819.8, 2))
    mn, mx = 0.0, 3000.0
    assert not (d > mn and d <= mx)
    assert not (d < mn or d > mx)


def test_missing_chainage_report():
    toc = pd.DataFrame({'FROM': ['S21', 'S3'], 'TO': ['S39', 'S21'],
                        'RT-PT-N': ['117/118', ''], 'RT-PT-R': ['', '123/124'],
                        'OV-PT-N': ['', ''], 'OV-PT-R': ['', '']})
    assert _missing_chainage(toc, CH) == ['PT118', 'PT123', 'PT124', 'S3']
