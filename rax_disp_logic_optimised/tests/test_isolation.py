"""Isolation points between simultaneous movements (single overshoot)."""
from helpers import make_toc
from rax_disp_logic_optimised.processing.isolation import find_isolation
from rax_disp_logic_optimised.processing.locking import ixl_fn
from rax_disp_logic_optimised.processing.point_format import parse_iso
from rax_disp_logic_optimised.processing.square_sheet import sqsh_fn, vice_versa
from rax_disp_logic_optimised.processing.toc_format import toc_format

NO_HS = [[''], [''], [''], ['']]
NO_END = ['', '', '', '']

# Two parallel lines joined by crossover 101/102.  Running left to right,
# point 101 is facing (toe on the 1BT side, normal leg 1CT, reverse leg to
# 102T); point 102 is trailing (reverse leg from 101T, toe on the 2CT side).
# Signal S13 stands at the end of 1BT.
#
#   line 1:  1AT - 1BT -|S13 101T - 1CT
#                              \
#   line 2:  2AT - 2BT ---- 102T - 2CT
#
LAYOUT = [
    dict(FROM='S1', TO='S9', UN='1', **{'RT-PT-N': '101/102N', 'RT-TC': '1AT, 1BT, 101T, 1CT'}),
    dict(FROM='S2', TO='S8', UN='2', **{'RT-PT-N': '101/102N', 'RT-TC': '2AT, 2BT, 102T, 2CT'}),
    dict(FROM='S3', TO='S7', UN='3', **{'RT-PT-R': '101/102R', 'RT-TC': '1BT, 101T, 102T, 2CT'}),
    # movements possible from the S13 post: straight on, or over the crossover
    dict(FROM='S13', TO='S19', UN='7', **{'RT-PT-N': '101/102N', 'RT-TC': '101T, 1CT'}),
    dict(FROM='S13', TO='S17', UN='8', **{'RT-PT-R': '101/102R', 'RT-TC': '101T, 102T, 2CT'}),
]
# Shunt (no overlap) up to S13: it overshoots along the S13 movements.
A = dict(FROM='SH11', TO='S13', UN='1', **{'RT-TC': '1AT, 1BT'})
# Movement whose ROUTE is 2CT (overlap beyond, on 2DT).
B = dict(FROM='S22', TO='S24', UN='2', **{'RT-TC': '2CT', 'OV-TC': '2DT'})
# Needs the crossover reversed but shares nothing with A or B.
X = dict(FROM='S4', TO='S6', UN='9', **{'RT-PT-R': '101/102R', 'RT-TC': '102T, 101T'})


def _run(rows, **kw):
    toc = toc_format(make_toc(rows), NO_HS, NO_END, 18)
    ltoc = ixl_fn(toc, NO_HS, NO_END, 18)
    vv = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
    return find_isolation(ltoc, vv, **kw), ltoc


def _row(res, a, b):
    r = res.report
    m = r[((r['MOVT-1'] == a) & (r['MOVT-2'] == b)) | ((r['MOVT-1'] == b) & (r['MOVT-2'] == a))]
    return None if m.empty else m.iloc[0]


def test_single_overshoot_through_facing_point_is_isolated():
    res, _ = _run(LAYOUT + [A, B])
    row = _row(res, 'SH11_1', 'S22_2')
    assert row is not None and row['RESULT'] == 'isolated'
    assert row['ISOLATION POINTS'] == '101/102N'
    assert row['OVERSHOOT PATH (track circuits)'] == \
        'SH11_1 overshoot past S13 via S13_8: 1BT-101T-102T-2CT at PT102 (distance not known)'
    assert frozenset(('SH11_1', 'S22_2')) in res.isolated
    # on the shunt it would lock out S13_8 (needs 101/102R); on S22_2 nothing
    iso = dict(zip(res.toc['FROM-TO'], res.toc['ISO-ADDED']))
    assert iso['S22_2'] == '101/102N (for SH11_1)' and iso['SH11_1'] == ''


def test_conditional_isolation_when_another_movement_needs_the_point():
    res, _ = _run(LAYOUT + [A, B, X])
    row = _row(res, 'SH11_1', 'S22_2')
    assert row['RESULT'] == 'isolated' and row['LOCKED AS'] == 'conditional'
    assert 'S4_9' in row['MOVTS NEWLY LOCKED (repercussion)']
    cond = dict(zip(res.toc['FROM-TO'], res.toc['COND-ISO']))
    assert cond['SH11_1'] == '#101/102N# with S22_2 set'
    assert cond['S22_2'] == '#101/102N# with SH11_1 set'
    assert (res.toc['ISO-ADDED'] == '').all()


def test_movement_with_overlap_stays_within_it():
    # same movement but with an overlap: its overshoot is the overlap
    a = dict(FROM='S11', TO='S13', UN='1', **{'RT-TC': '1AT', 'OV-TC': '1BT'})
    res, _ = _run(LAYOUT + [a, B])
    assert _row(res, 'S11_1', 'S22_2') is None


def test_double_overshoot_is_ignored():
    # the shunt's overshoot reaches only B's OVERLAP (2CT): B would have to
    # overshoot as well, so no isolation is needed
    b = dict(FROM='S22', TO='S24', UN='2', **{'RT-TC': '9AT', 'OV-TC': '2CT'})
    res, _ = _run(LAYOUT + [A, b])
    assert _row(res, 'SH11_1', 'S22_2') is None


def test_overshoot_follows_only_movements_from_the_next_signal_post():
    # shunt up to SH15, a post with no movements onward: nothing to overshoot along
    a = dict(A, TO='SH15')
    res, _ = _run(LAYOUT + [a, B])
    assert _row(res, 'SH11_1', 'S22_2') is None


def test_trailing_point_does_not_isolate():
    # shunt on line 2 up to S16; from S16 it can only run on through
    # trailing 102 to 2CT: nothing to lock, dispensation stays
    s16 = dict(FROM='S16', TO='S18', UN='6', **{'RT-PT-N': '101/102N', 'RT-TC': '102T, 2CT'})
    a = dict(FROM='SH12', TO='S16', UN='2', **{'RT-TC': '2AT, 2BT'})
    b = dict(FROM='S22', TO='S24', UN='4', **{'RT-TC': '2CT', 'OV-TC': '2DT'})
    res, _ = _run(LAYOUT + [s16, a, b])
    row = _row(res, 'SH12_2', 'S22_4')
    assert row is not None and row['RESULT'] == 'not isolable'


def test_distance_limit_from_next_stop_signal(tmp_path):
    import pandas as pd
    ch = tmp_path / 'ch.csv'
    pd.DataFrame({'GR_NO': ['S13', 'PT101', 'PT102'], 'GR_CH': [1000, 1050, 1060]}).to_csv(ch, index=False)
    res, _ = _run(LAYOUT + [A, B], chainage_path=str(ch), distance=120)
    row = _row(res, 'SH11_1', 'S22_2')
    assert row['RESULT'] == 'isolated' and 'at PT102 60 m from S13' in row['OVERSHOOT PATH (track circuits)']
    res, _ = _run(LAYOUT + [A, B], chainage_path=str(ch), distance=50)
    assert _row(res, 'SH11_1', 'S22_2') is None          # criss-cross point beyond 50 m


def test_iso_reverse_position_is_respected():
    # existing ISO 101/102R on the shunt sends it to line 2: cannot isolate
    a = dict(A, ISO='101/102R')
    res, _ = _run(LAYOUT + [a, B])
    assert _row(res, 'SH11_1', 'S22_2')['RESULT'] == 'not isolable'


def test_iso_without_position_means_normal():
    assert parse_iso('121, 111/112R, 142T') == ([('121', 'N'), ('111/112', 'R')], ['142T'])


def test_iso_display_keeps_reverse_letter():
    a = dict(A, ISO='121, 133/134R')
    _, ltoc = _run(LAYOUT + [a, B])
    assert ltoc.set_index('FROM-TO').at['SH11_1', 'ISO'] == '121, 133/134R'


def _dsp_pairs(path):
    import pandas as pd
    t = pd.read_excel(path, dtype=str).fillna('')
    return {frozenset((a, b.strip())) for a, cell in zip(t['FROM-TO'], t['NEW-DSP2'])
            for b in cell.split('\n') if b.strip()}, t


def test_pipeline_iso_preferred_removes_isolated_pair_from_dispensation(tmp_path):
    from rax_disp_logic_optimised.processing.pipeline import run_dispensation
    path = tmp_path / 'toc.xlsx'
    make_toc(LAYOUT + [A, B, X]).to_excel(path, index=False)
    pair = frozenset(('SH11_1', 'S22_2'))

    off = tmp_path / 'off'; off.mkdir()
    r0 = run_dispensation(str(path), str(off), 'T', NO_HS, NO_END, 18, iso_preferred=False, t_str='T')
    assert pair in _dsp_pairs(r0.paths['TOC'])[0] and 'ISO' not in r0.paths

    on = tmp_path / 'on'; on.mkdir()
    r1 = run_dispensation(str(path), str(on), 'T', NO_HS, NO_END, 18, iso_preferred=True, t_str='T')
    pairs, toc = _dsp_pairs(r1.paths['TOC'])
    assert pair not in pairs
    assert r1.isolation['isolated'] >= 1
    cond = dict(zip(toc['FROM-TO'], toc['COND-ISO']))
    assert cond['SH11_1'] == '#101/102N# with S22_2 set'
    import pandas as pd
    assert set(pd.ExcelFile(r1.paths['ISO']).sheet_names) >= {'SUMMARY', 'PAIRS', 'BY MOVEMENT'}


def test_pipeline_adds_unconditional_iso_to_locking(tmp_path):
    from rax_disp_logic_optimised.processing.pipeline import run_dispensation
    path = tmp_path / 'toc.xlsx'
    make_toc(LAYOUT + [A, B]).to_excel(path, index=False)
    r = run_dispensation(str(path), str(tmp_path), 'T', NO_HS, NO_END, 18, t_str='T')
    _, toc = _dsp_pairs(r.paths['TOC'])
    row = toc.set_index('FROM-TO')
    owner = 'S22_2'
    assert row.at[owner, 'ISO-ADDED'].startswith('101/102N')
    assert '101/102' in row.at[owner, 'ISO-PT-N']       # locked like any ISO point


def test_point_cannot_be_locked_both_ways():
    from rax_disp_logic_optimised.processing.isolation import _choose_points
    nil = lambda p, pos: 0
    # both legs of the only facing point lead onto the route: not isolable
    both_legs = [('A', ('146T',), {'141/142': 'N'}), ('A', ('142T', '134T'), {'141/142': 'R'})]
    assert _choose_points(both_legs, nil) is None
    # an earlier facing point cuts both paths (GRF CO21_01D vs S33_39)
    co21 = [('A', ('140T', '141T', '146T'), {'139/140': 'R', '141/142': 'N'}),
            ('A', ('140T', '141T', '142T', '134T'), {'139/140': 'R', '141/142': 'R'})]
    assert _choose_points(co21, nil) == [('139/140', 'N')]
