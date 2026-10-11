"""Jobs run in their own process (keeps the window / server responsive)."""
import time

from helpers import make_toc
from rax_disp_logic_optimised.service.jobs import (
    cancel_process, check_process, read_status, start_job_process,
)

ROWS = [
    dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-TC': '3T, 101T'}),
    dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '105/106N', 'RT-TC': '105T, 12T'}),
]


def _wait(proc, status, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = check_process(proc, status)
        if st.get('state') in ('done', 'failed', 'cancelled'):
            return st
        time.sleep(0.2)
    raise AssertionError('job did not finish')


def test_job_process_runs_pipeline(tmp_path):
    toc = tmp_path / 'toc.xlsx'
    make_toc(ROWS).to_excel(toc, index=False)
    status = str(tmp_path / 'st.json')
    p = start_job_process({'toc_path': str(toc), 'save_dir': str(tmp_path / 'out'),
                           'stn': 'TST', 'l_no': 18, 't_str': 'T'}, status)
    st = _wait(p, status)
    assert st['state'] == 'done', st.get('traceback')
    assert st['progress'] == 100 and '347' in st['paths']


def test_job_failure_is_reported(tmp_path):
    status = str(tmp_path / 'st.json')
    p = start_job_process({'toc_path': str(tmp_path / 'missing.xlsx'),
                           'save_dir': str(tmp_path), 'stn': 'X'}, status)
    st = _wait(p, status)
    assert st['state'] == 'failed' and st['error']


def test_cancel(tmp_path):
    toc = tmp_path / 'toc.xlsx'
    make_toc(ROWS).to_excel(toc, index=False)
    status = str(tmp_path / 'st.json')
    p = start_job_process({'toc_path': str(toc), 'save_dir': str(tmp_path), 'stn': 'X'}, status)
    cancel_process(p, status)
    assert not p.is_alive()
    assert read_status(status)['state'] == 'cancelled'
