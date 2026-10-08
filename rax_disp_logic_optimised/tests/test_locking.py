"""Conflict detection (ixl_fn) and the square sheet built from it."""
import pandas as pd

from helpers import allowed_pairs, cell, conflicts, run_pipeline
from rax_disp_logic_optimised.core.constants import RE_TRACK_CIRCUIT
from rax_disp_logic_optimised.processing.locking import un_key
from rax_disp_logic_optimised.processing.point_format import iso_pt_format
from rax_disp_logic_optimised.processing.square_sheet import (
    new_disp_gen_frm_sqsh, new_lck_gen_frm_sqsh, vice_versa,
)
from rax_disp_logic_optimised.processing.track_circuit import tc_format_fn


def test_un_key_ignores_line_suffix_and_leading_zeros():
    assert un_key('01D') == un_key('01M') == un_key('1') == '1'
    assert un_key('05M') == un_key('5M') == '5'
    assert un_key('JKCL2D') == un_key('JKCL2M') == 'JKCL2'
    assert un_key('DMABM1') == 'DMABM1'
    assert un_key('UMAB') == 'UMAB'
    assert un_key('') == ''


def test_track_circuit_pattern_accepts_slash_and_letter_names():
    for tc in ['229T', '03AT', '01AT', '240/250T', '238/241/252T', 'DMAT', 'JKCL1AT']:
        assert RE_TRACK_CIRCUIT.match(tc), tc
    for not_tc in ['AXT', '101N', '111/112', 'T', 'NIL']:
        assert not RE_TRACK_CIRCUIT.match(not_tc), not_tc


def test_tc_format_keeps_slash_track_circuits():
    toc = pd.DataFrame({'RT-TC': ['5T, 240/250T, 246T'], 'OV-TC': [''], 'ISO-TC': ['259/260T']})
    assert tc_format_fn(toc).iloc[0] == '5T, 240/250T, 246T, 259/260T'


def test_iso_points_keep_full_crossover_name():
    toc = pd.DataFrame({'ISO': ['111/112N, 121N, 135/136R, 142T']})
    pts = iso_pt_format(toc, 'ISO')
    assert pts.iloc[0] == '111/112N, 121N, 135/136R'
    assert toc.at[0, 'ISO-PT-N'] == '111/112, 121'
    assert toc.at[0, 'ISO-PT-R'] == '135/136'
    assert toc.at[0, 'ISO-TC'] == '142T'


def test_conflict_via_one_of_several_isolation_points_is_two_sided():
    # A needs 111/112 Normal for isolation (2nd of 2 iso points);
    # B needs 111/112 Reverse in its route.  Nothing else is shared.
    _, vv, dl, _ = run_pipeline([
        dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-TC': '3T, 101T',
                                              'ISO': '121N, 111/112N'}),
        dict(FROM='S18', TO='S12', UN='UMAB', **{'RT-PT-R': '111/112R', 'RT-TC': '112T'}),
    ])
    assert cell(vv, 'S3_01D', 'S18_UMAB') == 'X'
    assert cell(vv, 'S18_UMAB', 'S3_01D') == 'X'
    assert frozenset(('S3_01D', 'S18_UMAB')) not in allowed_pairs(dl)


def test_isolation_track_circuit_counts_for_locking():
    _, vv, _, _ = run_pipeline([
        dict(FROM='S17', TO='S33', UN='04M', **{'RT-PT-N': '107/108N', 'RT-TC': '107T',
                                               'ISO': '135/136N, 142T'}),
        dict(FROM='SH38', TO='S20', UN='04D', **{'RT-PT-N': '141/142N', 'RT-TC': '142T'}),
    ])
    assert conflicts(vv, 'S17_04M', 'SH38_04D')


def test_shared_slash_track_circuit_is_a_conflict():
    _, vv, dl, _ = run_pipeline([
        dict(FROM='S5', TO='S121', UN='01D', **{'RT-PT-N': '207/208N', 'RT-TC': '5T, 240/250T'}),
        dict(FROM='S9', TO='S87', UN='13', **{'RT-PT-N': '301/302N', 'RT-TC': '240/250T, 9T'}),
    ])
    assert cell(vv, 'S5_01D', 'S9_13') == 'X'
    assert frozenset(('S5_01D', 'S9_13')) not in allowed_pairs(dl)


def test_letter_named_track_circuit_is_a_conflict():
    _, vv, _, _ = run_pipeline([
        dict(FROM='S3', TO='S21', UN='DMABM1', **{'RT-PT-N': '101/102N', 'RT-TC': '3AT, DMAT'}),
        dict(FROM='SH51', TO='SH53', UN='WCSL', **{'RT-PT-N': '155/156N', 'RT-TC': 'DMAT'}),
    ])
    assert cell(vv, 'S3_DMABM1', 'SH51_WCSL') == 'X'


def test_routes_onto_same_line_conflict_even_with_d_m_suffix():
    _, vv, dl, _ = run_pipeline([
        dict(FROM='CO21', TO='S31', UN='01D', **{'RT-PT-N': '113/114N'}),
        dict(FROM='CO38', TO='S18', UN='01D', **{'RT-PT-N': '139/140N'}),
        dict(FROM='S19', TO='S35', UN='5M', **{'RT-PT-N': '109/110N'}),
        dict(FROM='S17', TO='S35', UN='05D', **{'RT-PT-N': '115/116N'}),
    ])
    assert cell(vv, 'CO21_01D', 'CO38_01D') == 'X'
    assert cell(vv, 'S19_5M', 'S17_05D') == 'X'
    assert frozenset(('CO21_01D', 'CO38_01D')) not in allowed_pairs(dl)


def test_independent_routes_are_allowed():
    _, vv, dl, _ = run_pipeline([
        dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '103/104N', 'RT-TC': '103T, 12T'}),
        dict(FROM='S45', TO='S49', UN='45', **{'RT-PT-N': '159/160N', 'RT-TC': '159T, 45T'}),
    ])
    assert cell(vv, 'S12_12', 'S45_45') == ''
    assert frozenset(('S12_12', 'S45_45')) in allowed_pairs(dl)


def _sheet(a_b, b_a):
    names = ['A_1', 'B_2', 'C_3']
    sq = pd.DataFrame('', index=names, columns=names)
    sq.loc['A_1', 'B_2'] = a_b
    sq.loc['B_2', 'A_1'] = b_a
    return sq


def test_vice_versa_marks_one_sided_conflict_on_both_cells():
    for a_b, b_a in [('X', ''), ('', 'X')]:
        vv = vice_versa(_sheet(a_b, b_a))
        assert vv.loc['A_1', 'B_2'] == '~' and vv.loc['B_2', 'A_1'] == '~'
        assert vv.loc['A_1', 'A_1'] == '#'
    vv = vice_versa(_sheet('X', 'X'))
    assert vv.loc['A_1', 'B_2'] == 'X' and vv.loc['B_2', 'A_1'] == 'X'


def test_one_sided_conflict_is_never_dispensed():
    # Either orientation, and even if '~' sits only below the diagonal.
    for a_b, b_a in [('X', ''), ('', 'X'), ('~', ''), ('', '~')]:
        sq = _sheet(a_b, b_a)
        for n in sq.index:
            sq.loc[n, n] = '#'
        ltoc = pd.DataFrame({'FROM-TO': list(sq.index)})
        dl = new_disp_gen_frm_sqsh(new_lck_gen_frm_sqsh(ltoc, sq, 'NEW-LCK2'), sq, 'NEW-DSP2')
        assert frozenset(('A_1', 'B_2')) not in allowed_pairs(dl), (a_b, b_a)
        assert frozenset(('A_1', 'C_3')) in allowed_pairs(dl)
    vv = vice_versa(_sheet('X', ''))
    ltoc = pd.DataFrame({'FROM-TO': list(vv.index)})
    lck = new_lck_gen_frm_sqsh(ltoc, vv, 'NEW-LCK2')['NEW-LCK2']
    assert 'B_2' in lck.iloc[0] and 'A_1' in lck.iloc[1]


def test_calling_on_route_uses_track_circuits_of_its_s_route():
    # CO21 has no RT-TC of its own; S21 to the same signal and line has 118T.
    # SH33 shares only 118T, so it must be locked against CO21 as well.
    ltoc, vv, dl, _ = run_pipeline([
        dict(FROM='S21', TO='S31', UN='01D', **{'RT-PT-N': '113/114N', 'RT-TC': '113T, 118T'}),
        dict(FROM='CO21', TO='S31', UN='01D', **{'RT-PT-N': '113/114N'}),
        dict(FROM='SH33', TO='SH45', UN='39', **{'RT-PT-N': '131/132N', 'RT-TC': '118T, 132T'}),
        dict(FROM='SH51', TO='SH53', UN='WCSL', **{'RT-PT-N': '155/156N', 'RT-TC': '155T'}),
    ])
    assert ltoc.set_index('FROM-TO').at['CO21_01D', 'TCK'] == '113T, 118T'
    assert ltoc.set_index('FROM-TO').at['CO21_01D', 'RT-TC'] == ''   # input left as is
    assert cell(vv, 'CO21_01D', 'SH33_39') == 'X'
    assert frozenset(('CO21_01D', 'SH33_39')) not in allowed_pairs(dl)
    assert frozenset(('CO21_01D', 'SH51_WCSL')) in allowed_pairs(dl)


def test_calling_on_route_keeps_its_own_track_circuits_if_given():
    from rax_disp_logic_optimised.processing.locking import co_track_circuits
    toc = pd.DataFrame({'FROM': ['S5', 'CO5', 'CO7', 'S101', 'CO101'],
                        'TO': ['S121', 'S121', 'S99', 'S137', 'S137'],
                        'UN': ['01D', '01D', '2', '444', '444'],
                        'RT-TC': ['5T, 208T', '01AT', '', '411/413T,444T', '_']})
    # own TCs kept; no S route -> stays blank; '_' placeholder counts as blank
    assert co_track_circuits(toc).tolist() == ['5T, 208T', '01AT', '', '411/413T,444T', '411/413T,444T']


def test_formatted_toc_ignores_output_columns_of_an_earlier_run(tmp_path):
    from rax_disp_logic_optimised.core.constants import ALL_TOC_COLS
    from rax_disp_logic_optimised.io.preprocessor import detect_toc_format, read_formatted_toc
    row = {c: '' for c in ALL_TOC_COLS}
    row.update({'FROM': 'S3', 'TO': 'S21', 'GN': 'S3', 'UN': '01D', 'RT-PT-N': '101/102N',
                'NEW-LOCK': 'S9_9', 'NEW-LCK2': 'CO3-DMABM1, SH38-02', 'TCK': '999T',
                'END': 'OLD', 'MN-MOVT-LIT': 'old text'})
    row['NEW-DSP2'] = 'S12_12'
    path = tmp_path / 'toc.xlsx'
    pd.DataFrame([row]).to_excel(path, index=False)
    assert detect_toc_format(str(path))
    df = read_formatted_toc(str(path)).fillna('')
    for col in ['NEW-LOCK', 'NEW-LCK2', 'TCK', 'END', 'MN-MOVT-LIT', 'NEW-DSP2']:
        assert (df[col].astype(str) == '').all(), col
    assert df.at[0, 'RT-PT-N'] == '101/102N'


def test_direction_odd_signal_is_dn_even_is_up():
    from helpers import make_toc
    from rax_disp_logic_optimised.processing.toc_format import toc_format
    rows = [dict(FROM=f, TO='S2', UN='1') for f in ['S3', 'CO21', 'SH7', 'S38', 'CO46', 'SH42', 'S10A', 'S11B']]
    toc = toc_format(make_toc(rows), [[''], [''], [''], ['']], ['', '', '', ''], 18)
    assert toc['DIR'].tolist() == ['DN', 'DN', 'DN', 'UP', 'UP', 'UP', 'UP', 'DN']
