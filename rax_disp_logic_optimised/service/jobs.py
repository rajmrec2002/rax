"""
Run one dispensation job in its own process.

The GUI and the server both start jobs here.  The job process writes its
state to a small JSON status file that the caller polls, so a long run
(criss-cross on a big yard can take minutes of CPU) never blocks the
window or the web server.

Job parameters (all JSON-serialisable):

    toc_path, save_dir, stn            str
    hs, end                            4 strings each, as typed in the GUI
                                       ('S5,CO5,S7', 'VIRAR')
    l_no                               int (berthing lines)
    vv_file                            str ('' = compute the square sheet)
    criss_cross                        bool
    chainage_path                      str
    bands                              '0-120, 120-300, 300+'  or [[lo, hi], ...]
    extra_chainage                     {'S12': 1234.5, ...}
    iso_preferred                      bool (default True)
    iso_distance                       metres (default 120; '' = no limit)
    t_str                              optional time stamp for file names
"""

import json
import math
import multiprocessing as mp
import os
import platform
import signal
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

STATES_FINAL = ('done', 'failed', 'cancelled')


def write_status(path: str, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into the status file (atomic replace)."""
    st = read_status(path)
    st.update(fields)
    st['updated'] = time.time()
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1, default=str)
    os.replace(tmp, path)
    return st


def read_status(path: str) -> Dict[str, Any]:
    for _ in range(3):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05)       # being replaced; try again
    return {}


def _bands(value):
    from ..processing.pipeline import parse_bands
    if not value:
        return None
    if isinstance(value, str):
        return parse_bands(value)
    return [(float(lo), math.inf if hi in (None, 'inf', math.inf) else float(hi))
            for lo, hi in value]


def run_job(params: Dict[str, Any], status_path: str) -> Dict[str, Any]:
    """Run the pipeline and record progress/result in `status_path`."""
    from ..processing.pipeline import parse_home_signals, run_dispensation

    write_status(status_path, state='running', progress=0, started=time.time(),
                 pid=os.getpid(), error='')
    last = [-1]

    def progress(p: float) -> None:
        p = int(max(0, min(100, p)))
        if p != last[0]:
            last[0] = p
            write_status(status_path, progress=p)

    try:
        hs, end = parse_home_signals(params.get('hs', [''] * 4), params.get('end', [''] * 4))
        os.makedirs(params['save_dir'], exist_ok=True)
        res = run_dispensation(
            params['toc_path'], params['save_dir'], params.get('stn', ''), hs, end,
            int(params.get('l_no', 1)),
            vv_file=params.get('vv_file', ''),
            criss_cross=bool(params.get('criss_cross')),
            chainage_path=params.get('chainage_path', ''),
            bands=_bands(params.get('bands')),
            extra_chainage=params.get('extra_chainage') or None,
            iso_preferred=bool(params.get('iso_preferred', True)),
            iso_distance=(None if params.get('iso_distance') in (None, '')
                          else float(params['iso_distance'])),
            progress=progress,
            t_str=params.get('t_str'),
        )
        checks = res.checks
        return write_status(
            status_path, state='done', progress=100, finished=time.time(),
            paths=res.paths, missing_chainage=res.missing_chainage,
            isolation=res.isolation,
            checks=len(checks),
            check_summary=(checks.groupby('CHECK').size().to_dict() if len(checks) else {}),
        )
    except Exception as e:      # reported to the caller, not raised
        return write_status(status_path, state='failed', finished=time.time(),
                            error=f'{type(e).__name__}: {e}',
                            traceback=traceback.format_exc())


class JobProcess:
    """A job running as `python -m rax_disp_logic_optimised.service.jobs`."""

    def __init__(self, popen: subprocess.Popen):
        self.popen = popen
        self.pid = popen.pid

    def is_alive(self) -> bool:
        return self.popen.poll() is None

    @property
    def exitcode(self):
        return self.popen.poll()

    def join(self, timeout: Optional[float] = None) -> None:
        try:
            self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def terminate(self) -> None:
        if self.is_alive():
            if hasattr(os, 'killpg'):
                try:
                    os.killpg(self.pid, signal.SIGTERM)   # also stops the criss-cross pool
                except OSError:
                    pass
            self.popen.terminate()

    def kill(self) -> None:
        if self.is_alive():
            if hasattr(os, 'killpg'):
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except OSError:
                    pass
            self.popen.kill()


def start_job_process(params: Dict[str, Any], status_path: str) -> JobProcess:
    """
    Start run_job in a new Python process and return it.

    A fresh interpreter (not a fork of the GUI or the threaded web server,
    and not a re-import of the caller's main script); the job itself may
    still fork a worker pool for criss-cross.
    """
    write_status(status_path, state='queued', progress=0, error='')
    params_path = status_path + '.params.json'
    with open(params_path, 'w', encoding='utf-8') as f:
        json.dump(params, f)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ)
    env['PYTHONPATH'] = pkg_parent + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    kw: Dict[str, Any] = {}
    if os.name == 'posix':
        kw['start_new_session'] = True        # own process group for cancel
    elif hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kw['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    popen = subprocess.Popen(
        [sys.executable, '-m', 'rax_disp_logic_optimised.service.jobs', params_path, status_path],
        env=env, stdin=subprocess.DEVNULL, **kw)
    proc = JobProcess(popen)
    write_status(status_path, pid=proc.pid)
    return proc


def check_process(proc: Optional[JobProcess], status_path: str) -> Dict[str, Any]:
    """Status of a job; marks it failed if its process died without finishing."""
    st = read_status(status_path)
    if st.get('state') not in STATES_FINAL and proc is not None and not proc.is_alive():
        st = read_status(status_path)             # it may have just finished
        if st.get('state') not in STATES_FINAL:
            st = write_status(status_path, state='failed', finished=time.time(),
                              error=f'Job process stopped unexpectedly (exit code {proc.exitcode})')
    return st


def cancel_process(proc: Optional[JobProcess], status_path: str) -> None:
    if proc is not None and proc.is_alive():
        proc.terminate()
        proc.join(5)
        proc.kill()
        proc.join(5)
    write_status(status_path, state='cancelled', finished=time.time(), error='Cancelled')


def _main(argv: List[str]) -> int:
    params_path, status_path = argv[1], argv[2]
    try:
        from ..utils.logging_util import setup_logging
        setup_logging()
    except Exception:
        pass
    with open(params_path, encoding='utf-8') as f:
        params = json.load(f)
    st = run_job(params, status_path)
    try:
        os.remove(params_path)
    except OSError:
        pass
    return 0 if st.get('state') == 'done' else 1


if __name__ == '__main__':
    if platform.system() == 'Linux':
        mp.set_start_method('fork', force=True)
    sys.exit(_main(sys.argv))
