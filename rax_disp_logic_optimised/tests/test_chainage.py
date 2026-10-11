"""Missing chainage is reported (and can be supplied) instead of silently locking pairs."""
import pandas as pd
import pytest

from helpers import make_toc
from rax_disp_logic_optimised.processing.pipeline import (
    check_chainage, parse_bands, parse_chainage_text, run_dispensation,
)

NO_HS = [[''], [''], [''], ['']]
NO_END = ['', '', '', '']
ROWS = [
    dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-TC': '3T, 101T'}),
    dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '105/106N', 'RT-TC': '105T, 12T'}),
    dict(FROM='S14', TO='S4', UN='14', **{'RT-PT-N': '107/108N', 'RT-TC': '107T, 14T'}),
]


@pytest.fixture
def files(tmp_path):
    toc = tmp_path / 'toc.xlsx'
    make_toc(ROWS).to_excel(toc, index=False)
    ch = tmp_path / 'ch.csv'
    pd.DataFrame({'GR_NO': ['S2', 'S4', 'S12', 'S14', 'S21', 'PT101', 'PT102', 'PT105', 'PT106'],
                  'GR_CH': [0.0, 50.0, 100.0, 400.0, 900.0, 950, 960, 120, 110]}).to_csv(ch, index=False)
    return str(toc), str(ch)


def test_parse_chainage_text():
    assert parse_chainage_text('S12, 1234.5\npt101 = 1300\n# note\nS9,\n') == \
        {'S12': 1234.5, 'PT101': 1300.0}
    with pytest.raises(ValueError):
        parse_chainage_text('S12, abc')


def test_check_chainage_lists_missing_entries(files):
    toc, ch = files
    assert check_chainage(toc, ch) == ['PT107', 'PT108', 'S3']
    assert check_chainage(toc, ch, {'S3': 1000}) == ['PT107', 'PT108']
    no_file = check_chainage(toc, '')
    assert {'S3', 'S12', 'PT101', 'PT107'} <= set(no_file)


def test_run_reports_missing_chainage_and_writes_template(files, tmp_path):
    toc, ch = files
    out = tmp_path / 'out'; out.mkdir()
    res = run_dispensation(toc, str(out), 'TST', NO_HS, NO_END, 18, criss_cross=True,
                           chainage_path=ch, bands=parse_bands('0-3000'), t_str='T')
    assert res.missing_chainage == ['PT107', 'PT108', 'S3']
    tpl = pd.read_csv(res.paths['CH-MISSING'])
    assert tpl['GR_NO'].tolist() == ['PT107', 'PT108', 'S3']
    chk = pd.read_excel(res.paths['TOC-CHECK'])
    assert set(chk.loc[chk['SET'] == 'chainage', 'ROUTE']) == {'PT107', 'PT108', 'S3'}


def test_extra_chainage_is_used_and_recorded(files, tmp_path):
    toc, ch = files
    out = tmp_path / 'out'; out.mkdir()
    res = run_dispensation(toc, str(out), 'TST', NO_HS, NO_END, 18, criss_cross=True,
                           chainage_path=ch, bands=parse_bands('0-3000'),
                           extra_chainage={'S3': 1000.0, 'PT107': 420.0, 'PT108': 430.0}, t_str='T')
    assert res.missing_chainage == []
    used = pd.read_csv(res.paths['CH-USED'])
    assert dict(zip(used['GR_NO'], used['GR_CH']))['S3'] == 1000.0
    assert 'CH-MISSING' not in res.paths
    # the user's chainage file is not touched
    assert 'S3' not in pd.read_csv(ch)['GR_NO'].tolist()
