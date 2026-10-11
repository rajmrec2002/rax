"""Isolation points between simultaneous movements (single overshoot)."""
import pandas as pd
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
    assert res.cond['SH11_1'] == {('101/102', 'N'): {'S22_2'}}
    assert res.cond['S22_2'] == {('101/102', 'N'): {'SH11_1'}}
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
    # same direction: a rear-end case, so considered only with rear_end=True
    res, _ = _run(LAYOUT + [s16, a, b], rear_end=True)
    row = _row(res, 'SH12_2', 'S22_4')
    assert row is not None and row['RESULT'] == 'not isolable'


# Movement in the shunt's own direction whose route starts at 101T, beyond S13:
# the shunt's overshoot can only run on behind it (rear-end), never across it.
AHEAD = dict(FROM='S15', TO='S21', UN='5', **{'RT-PT-N': '101/102N', 'RT-TC': '101T, 1CT',
                                              'OV-TC': '1DT'})


def test_rear_end_is_not_considered_by_default():
    res, _ = _run(LAYOUT + [A, AHEAD])
    row = _row(res, 'SH11_1', 'S15_5')
    assert row is not None and row['RESULT'] == 'rear-end ignored'
    assert 'then on behind S15_5' in row['OVERSHOOT PATH (track circuits)']
    assert frozenset(('SH11_1', 'S15_5')) not in res.isolated
    assert res.summary['rear-end ignored'] >= 1


def test_rear_end_considered_when_asked():
    res, _ = _run(LAYOUT + [A, AHEAD], rear_end=True)
    row = _row(res, 'SH11_1', 'S15_5')
    assert row is not None and row['RESULT'] != 'rear-end ignored'
    assert res.summary.get('rear-end ignored', 0) == 0


def test_opposite_direction_meeting_is_not_rear_end():
    # SH11 (odd, DN) and S22 (even, UP) meet head-on in 2CT: always considered
    res, _ = _run(LAYOUT + [A, B])
    assert _row(res, 'SH11_1', 'S22_2')['RESULT'] == 'isolated'


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
    # conditional isolation merged into ISO-PT-N with its condition
    isn = dict(zip(toc['FROM-TO'], toc['ISO-PT-N']))
    assert isn['SH11_1'] == '#101/102# with S22_2'
    assert isn['S22_2'] == '#101/102# with SH11_1'
    # ... and it does not lock S4_9 (needs 101/102R) against the shunt
    sq = pd.read_excel(r1.paths['SQSH'], index_col=0).fillna('')
    assert sq.at['SH11_1', 'S4_9'] not in ('X', '~')
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


def test_point_track_circuits_have_three_digit_numbers():
    from rax_disp_logic_optimised.processing.isolation import _point_ends
    assert _point_ends('121T') == ['121']
    assert _point_ends('107/108T') == ['107', '108']
    assert _point_ends('01AT') == [] and _point_ends('3BT') == [] and _point_ends('JKCL1AT') == []
    assert _point_ends('581T/ALT') == ['581']


def test_facing_by_point_number_and_direction():
    from rax_disp_logic_optimised.processing.isolation import _facing
    assert _facing('101', 'DN') is True and _facing('102', 'DN') is False
    assert _facing('102', 'UP') is True and _facing('121', 'UP') is False
    assert _facing('121', '') is None


def test_shunt_takes_the_berthing_of_its_post_to_post_movement():
    from rax_disp_logic_optimised.processing.isolation import _routes
    rows = [dict(FROM='S21', TO='S31', UN='01D', **{'RT-PT-N': '113/114N', 'RT-TC': '113T, 01AT, 01BT'}),
            dict(FROM='SH21', TO='SH31', UN='01D', **{'RT-PT-N': '113/114N', 'RT-TC': '113T'})]
    _, ltoc = _run(rows)
    r = {x.name: x for x in _routes(ltoc)}
    assert r['SH21_01D'].rt_seq == ['113T', '01AT', '01BT']


def test_trailing_end_of_crossover_gives_no_isolation_point():
    # UP movement (even signals) over the same crossover: 101 is trailing for
    # it, so an overshoot that only passes 101 cannot be turned away there
    s14 = dict(FROM='S14', TO='S20', UN='6', **{'RT-PT-R': '101/102R', 'RT-TC': '102T, 101T, 1BT'})
    a = dict(FROM='SH12', TO='S14', UN='2', **{'RT-TC': '2DT, 2CT'})
    b = dict(FROM='S30', TO='S32', UN='5', **{'RT-TC': '1BT', 'OV-TC': '1AT'})
    res, _ = _run(LAYOUT + [s14, a, b], rear_end=True)
    row = _row(res, 'SH12_2', 'S30_5')
    # facing end for UP is 102 (even): that is where it can be turned away
    assert row is not None and row['ISOLATION POINTS'] == '101/102N'
    assert 'at PT' in row['OVERSHOOT PATH (track circuits)']


def test_pair_covered_by_iso_added_for_another_pair_counts_as_isolated():
    # a second movement on the same line, same post-to-post path as the shunt
    a2 = dict(FROM='A11', TO='S13', UN='1', **{'RT-TC': '1AT, 1BT'})
    res, _ = _run(LAYOUT + [A, B, a2])
    first = _row(res, 'SH11_1', 'S22_2')
    second = _row(res, 'A11_1', 'S22_2')
    assert first['RESULT'] == 'isolated' and first['LOCKED AS'] == 'ISO of S22_2'
    assert second['RESULT'] == 'isolated' and second['LOCKED AS'] == 'already in ISO of S22_2'
    assert frozenset(('A11_1', 'S22_2')) in res.isolated


def test_conditional_iso_is_not_locked_pairwise_and_not_printed():
    """Readers of ISO-PT-N / ISO-PT-R use only the unconditional points."""
    import pandas as pd
    from rax_disp_logic_optimised.processing.dispensation import iso_text
    from rax_disp_logic_optimised.processing.point_format import iso_split, iso_uncond
    cell = '121, #101/102# with S22_2+S24_4'
    assert iso_uncond(cell) == '121'
    assert iso_split(cell) == (['121'], {'101/102': ['S22_2', 'S24_4']})
    t = pd.DataFrame({'ISO-PT-N': [cell], 'ISO-PT-R': [''], 'ISO-TC': ['']})
    assert iso_text(t, 0) == ' with isolation point/s (121N)'
    # the locking ignores the conditional point
    toc = toc_format(make_toc(LAYOUT + [dict(A, ISO=''), B, X]), NO_HS, NO_END, 18)
    toc['ISO-PT-N'] = ['', '', '', '', '', '#101/102# with S22_2', '', '']
    ltoc = ixl_fn(toc, NO_HS, NO_END, 18)
    vv = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
    assert vv.at['SH11_1', 'S4_9'] not in ('X', '~')


def test_track_circuit_of_calling_on_signal_is_not_running_track():
    from rax_disp_logic_optimised.processing.isolation import _routes
    rows = [dict(FROM='S5', TO='S11', UN='1', **{'RT-PT-N': '205/206N', 'RT-TC': '205T, CO5T, 01AT'}),
            dict(FROM='CO5', TO='S11', UN='1', **{'RT-PT-N': '205/206N'}),
            dict(FROM='S7', TO='CO9', UN='2', **{'RT-TC': '207T, CO9T'})]     # CO9 is a TO signal
    _, ltoc = _run(rows)
    r = {x.name: x for x in _routes(ltoc)}
    assert r['S5_1'].rt_seq == ['205T', '01AT']
    assert r['CO5_1'].rt_seq == ['205T', '01AT']
    assert r['S7_2'].rt_seq == ['207T', 'CO9T']


def test_overshoot_runs_back_over_movements_onto_the_shared_berthing():
    # GRF CO38 -> S30 / S3 -> S21: no movement S30 -> S21 in the TOC, but the
    # DN movement S21 -> S31 berths on the same line (01AT, 01BT) as the UP
    # shunt to S30, so the shunt's overshoot can run back over its track
    # (124T, 123T, 113T) and on, back over S3 -> S21, into S3's train.
    rows = [
        dict(FROM='S21', TO='S31', UN='1', **{'RT-PT-R': '123/124R',
                                               'RT-TC': '113T, 123T, 124T, 01AT, 01BT',
                                               'OV-TC': '139T'}),
        dict(FROM='S21', TO='S39', UN='2', **{'RT-PT-N': '123/124N',
                                               'RT-TC': '113T, 123T, 02AT, 02BT', 'OV-TC': '140T'}),
        dict(FROM='S3', TO='S21', UN='9', **{'RT-TC': '3AT, DMAT, DMBT', 'OV-TC': '113T'}),
        dict(FROM='SH38', TO='S30', UN='1', **{'RT-TC': '139T, 01BT, 01AT'}),
    ]
    # no chainage: chaining past the first movement needs distance=None
    res, _ = _run(rows, distance=None)
    row = _row(res, 'SH38_1', 'S3_9')
    assert row is not None and row['RESULT'] == 'isolated'
    assert row['ISOLATION POINTS'] == '123/124N'
    path = row['OVERSHOOT PATH (track circuits)']
    assert 'S21_1 backwards' in path and '124T-123T-113T-DMBT' in path
