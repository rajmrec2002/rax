"""Isolation points between simultaneous movements."""
from helpers import make_toc
from rax_disp_logic_optimised.processing.isolation import find_isolation
from rax_disp_logic_optimised.processing.locking import ixl_fn
from rax_disp_logic_optimised.processing.point_format import parse_iso
from rax_disp_logic_optimised.processing.square_sheet import sqsh_fn, vice_versa
from rax_disp_logic_optimised.processing.toc_format import toc_format

NO_HS = [[''], [''], [''], ['']]
NO_END = ['', '', '', '']

# Two parallel lines joined by crossover 101/102 (101T on line 1, 102T on line 2):
#
#   line 1:  1AT - 1BT - 101T - 1CT
#                          \
#   line 2:  2AT - 2BT - 102T - 2CT
#
LAYOUT = [
    dict(FROM='S1', TO='S9', UN='1', **{'RT-PT-N': '101/102N', 'RT-TC': '1AT, 1BT, 101T, 1CT'}),
    dict(FROM='S2', TO='S8', UN='2', **{'RT-PT-N': '101/102N', 'RT-TC': '2AT, 2BT, 102T, 2CT'}),
    dict(FROM='S3', TO='S7', UN='2', **{'RT-PT-R': '101/102R', 'RT-TC': '1BT, 101T, 102T, 2CT'}),
]
# Movement on line 1 that stops before the crossover, and one on line 2 beyond it.
A = dict(FROM='S11', TO='S13', UN='1', **{'RT-TC': '1AT, 1BT'})
B = dict(FROM='S22', TO='S24', UN='2', **{'RT-TC': '2CT'})
# A movement that needs the crossover reversed but shares nothing with A or B.
X = dict(FROM='S4', TO='S6', UN='9', **{'RT-PT-R': '101/102R', 'RT-TC': '102T, 101T'})


def _run(rows):
    toc = toc_format(make_toc(rows), NO_HS, NO_END, 18)
    ltoc = ixl_fn(toc, NO_HS, NO_END, 18)
    vv = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
    return find_isolation(ltoc, vv), ltoc


def _row(res, a, b):
    r = res.report
    m = r[((r['MOVT-1'] == a) & (r['MOVT-2'] == b)) | ((r['MOVT-1'] == b) & (r['MOVT-2'] == a))]
    assert len(m) == 1, r.to_string()
    return m.iloc[0]


def test_isolation_point_added_to_iso_when_nothing_else_needs_it():
    res, _ = _run(LAYOUT + [A, B])
    row = _row(res, 'S11_1', 'S22_2')
    assert row['RESULT'] == 'isolated'
    assert row['ISOLATION POINTS'] == '101/102N'
    assert frozenset(('S11_1', 'S22_2')) in res.isolated
    iso = dict(zip(res.toc['FROM-TO'], res.toc['ISO-ADDED']))
    assert iso['S11_1'].startswith('101/102N') or iso['S22_2'].startswith('101/102N')


def test_conditional_isolation_when_another_movement_needs_the_point():
    res, _ = _run(LAYOUT + [A, B, X])
    row = _row(res, 'S11_1', 'S22_2')
    assert row['RESULT'] == 'isolated' and row['LOCKED AS'] == 'conditional'
    assert 'S4_9' in row['MOVTS NEWLY LOCKED (repercussion)']
    cond = dict(zip(res.toc['FROM-TO'], res.toc['COND-ISO']))
    assert cond['S11_1'] == '#101/102N# with S22_2 set'
    assert cond['S22_2'] == '#101/102N# with S11_1 set'
    assert (res.toc['ISO-ADDED'] == '').all()


def test_movements_already_separated_by_their_own_points_need_nothing():
    a = dict(A, **{'RT-PT-N': '101/102N', 'RT-TC': '1AT, 1BT, 101T'})
    res, _ = _run(LAYOUT + [a, B])
    assert res.report.empty or not ((res.report['MOVT-1'] == 'S11_1')
                                    & (res.report['MOVT-2'] == 'S22_2')).any()
    assert frozenset(('S11_1', 'S22_2')) not in res.isolated


def test_plain_track_connection_cannot_be_isolated():
    # B starts on the track right after A's last track circuit: no point between
    b = dict(B, **{'RT-TC': '1CT'})
    a = dict(A, **{'RT-TC': '101T', 'RT-PT-N': '101/102N'})
    res, _ = _run(LAYOUT + [a, b])
    row = _row(res, 'S11_1', 'S22_2')
    assert row['RESULT'] == 'not isolable'


def test_iso_without_position_means_normal():
    assert parse_iso('121, 111/112R, 142T') == ([('121', 'N'), ('111/112', 'R')], ['142T'])


def _dsp_pairs(path):
    import pandas as pd
    t = pd.read_excel(path, dtype=str).fillna('')
    return {frozenset((a, b.strip())) for a, cell in zip(t['FROM-TO'], t['NEW-DSP2'])
            for b in cell.split('\n') if b.strip()}, t


def test_pipeline_iso_preferred_removes_isolated_pair_from_dispensation(tmp_path):
    from rax_disp_logic_optimised.processing.pipeline import run_dispensation
    path = tmp_path / 'toc.xlsx'
    make_toc(LAYOUT + [A, B, X]).to_excel(path, index=False)
    pair = frozenset(('S11_1', 'S22_2'))

    off = tmp_path / 'off'; off.mkdir()
    r0 = run_dispensation(str(path), str(off), 'T', NO_HS, NO_END, 18, iso_preferred=False, t_str='T')
    assert pair in _dsp_pairs(r0.paths['TOC'])[0] and 'ISO' not in r0.paths

    on = tmp_path / 'on'; on.mkdir()
    r1 = run_dispensation(str(path), str(on), 'T', NO_HS, NO_END, 18, iso_preferred=True, t_str='T')
    pairs, toc = _dsp_pairs(r1.paths['TOC'])
    assert pair not in pairs
    assert r1.isolation['isolated'] >= 1
    cond = dict(zip(toc['FROM-TO'], toc['COND-ISO']))
    assert cond['S11_1'] == '#101/102N# with S22_2 set'
    import pandas as pd
    assert set(pd.ExcelFile(r1.paths['ISO']).sheet_names) >= {'SUMMARY', 'PAIRS', 'BY MOVEMENT'}


def test_pipeline_adds_unconditional_iso_to_locking(tmp_path):
    from rax_disp_logic_optimised.processing.pipeline import run_dispensation
    path = tmp_path / 'toc.xlsx'
    make_toc(LAYOUT + [A, B]).to_excel(path, index=False)
    r = run_dispensation(str(path), str(tmp_path), 'T', NO_HS, NO_END, 18, t_str='T')
    _, toc = _dsp_pairs(r.paths['TOC'])
    row = toc.set_index('FROM-TO')
    owner = 'S11_1' if row.at['S11_1', 'ISO-ADDED'] else 'S22_2'
    assert row.at[owner, 'ISO-ADDED'].startswith('101/102N')
    assert '101/102' in row.at[owner, 'ISO-PT-N']       # locked like any ISO point


def test_iso_reverse_position_is_respected():
    # existing ISO 101/102R on A keeps the crossover open: cannot isolate with 101/102N
    a = dict(A, ISO='101/102R')
    res, _ = _run(LAYOUT + [a, B])
    row = _row(res, 'S11_1', 'S22_2')
    assert row['RESULT'] == 'not isolable'


def test_iso_display_keeps_reverse_letter():
    a = dict(A, ISO='121, 133/134R')
    _, ltoc = _run(LAYOUT + [a, B])
    assert ltoc.set_index('FROM-TO').at['S11_1', 'ISO'] == '121, 133/134R'
