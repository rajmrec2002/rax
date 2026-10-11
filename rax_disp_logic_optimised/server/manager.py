"""
Job queue for the server.

Each job is a folder  <data_dir>/jobs/<id>/  holding
    input/      uploaded TOC, chainage and VV files
    output/     everything the run writes
    job.json    parameters
    status.json state, progress and result (see service.jobs)

At most `workers` jobs run at once; the rest wait as 'queued'.  Jobs
survive a server restart: queued ones are started again, ones that were
running are marked failed.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional

from ..service.jobs import (
    STATES_FINAL, cancel_process, check_process, read_status, start_job_process, write_status,
)
from .config import ServerConfig

logger = logging.getLogger(__name__)

_SAFE = re.compile(r'[^A-Za-z0-9._+-]+')


def safe_name(name: str, default: str = 'file') -> str:
    name = _SAFE.sub('_', os.path.basename(str(name or '')).strip()).strip('._')
    return name or default


class JobManager:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        os.makedirs(cfg.jobs_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._procs: Dict[str, Any] = {}
        self._stop = threading.Event()
        self._recover()
        self._thread = threading.Thread(target=self._loop, name='rax-dispatch', daemon=True)
        self._thread.start()

    # ── paths ────────────────────────────────────────────────────────────
    def job_dir(self, job_id: str) -> str:
        if not re.fullmatch(r'[0-9a-f]{12}', job_id or ''):
            raise KeyError(job_id)
        return os.path.join(self.cfg.jobs_dir, job_id)

    def _status_path(self, job_id: str) -> str:
        return os.path.join(self.job_dir(job_id), 'status.json')

    # ── create / read ────────────────────────────────────────────────────
    def create(self, params: Dict[str, Any], files: Dict[str, tuple]) -> str:
        """
        files: {'toc': (filename, bytes), 'chainage': (...), 'vv': (...)}
        params: job parameters without paths (see service.jobs).
        """
        job_id = uuid.uuid4().hex[:12]
        d = os.path.join(self.cfg.jobs_dir, job_id)
        os.makedirs(os.path.join(d, 'input'))
        os.makedirs(os.path.join(d, 'output'))
        params = dict(params)
        for key, target in (('toc', 'toc_path'), ('chainage', 'chainage_path'), ('vv', 'vv_file')):
            if key in files and files[key]:
                fname, data = files[key]
                path = os.path.join(d, 'input', f'{key}_{safe_name(fname, key)}')
                with open(path, 'wb') as f:
                    f.write(data)
                params[target] = path
            else:
                params[target] = ''
        params['save_dir'] = os.path.join(d, 'output')
        params['stn'] = safe_name(params.get('stn', ''), 'STN')
        with open(os.path.join(d, 'job.json'), 'w', encoding='utf-8') as f:
            json.dump(params, f, indent=1)
        write_status(os.path.join(d, 'status.json'), id=job_id, state='queued', progress=0,
                     created=time.time(), stn=params['stn'],
                     criss_cross=bool(params.get('criss_cross')),
                     iso_preferred=bool(params.get('iso_preferred', True)), error='')
        logger.info('Job %s queued (%s)', job_id, params['stn'])
        return job_id

    def params(self, job_id: str) -> Dict[str, Any]:
        with open(os.path.join(self.job_dir(job_id), 'job.json'), encoding='utf-8') as f:
            return json.load(f)

    def status(self, job_id: str) -> Dict[str, Any]:
        d = self.job_dir(job_id)
        if not os.path.isdir(d):
            raise KeyError(job_id)
        st = read_status(self._status_path(job_id))
        st['id'] = job_id
        out = os.path.join(d, 'output')
        st['files'] = sorted(os.listdir(out)) if os.path.isdir(out) else []
        # server paths are not useful to clients: give the file names
        st['outputs'] = {k: os.path.basename(v) for k, v in (st.pop('paths', None) or {}).items()}
        st.pop('traceback', None)
        st.pop('pid', None)
        if st.get('state') == 'queued':
            st['queue_position'] = self._queue().index(job_id) + 1 if job_id in self._queue() else 0
        return st

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        ids = [j for j in os.listdir(self.cfg.jobs_dir) if re.fullmatch(r'[0-9a-f]{12}', j)]
        rows = []
        for j in ids:
            try:
                rows.append(self.status(j))
            except Exception:
                continue
        rows.sort(key=lambda r: r.get('created', 0), reverse=True)
        return rows[:limit]

    def output_file(self, job_id: str, name: str) -> str:
        out = os.path.join(self.job_dir(job_id), 'output')
        path = os.path.join(out, os.path.basename(name))
        if not os.path.isfile(path):
            raise KeyError(name)
        return path

    def zip_outputs(self, job_id: str) -> str:
        d = self.job_dir(job_id)
        out = os.path.join(d, 'output')
        zpath = os.path.join(d, 'outputs.zip')
        st = read_status(self._status_path(job_id))
        if not os.path.isfile(zpath) or os.path.getmtime(zpath) < st.get('updated', 0):
            tmp = zpath + '.tmp'
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
                for f in sorted(os.listdir(out)):
                    z.write(os.path.join(out, f), f)
            os.replace(tmp, zpath)
        return zpath

    # ── cancel / delete ──────────────────────────────────────────────────
    def cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            sp = self._status_path(job_id)
            if read_status(sp).get('state') not in STATES_FINAL:
                cancel_process(self._procs.pop(job_id, None), sp)
        return self.status(job_id)

    def delete(self, job_id: str) -> None:
        self.cancel(job_id)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    # ── scheduling ───────────────────────────────────────────────────────
    def _queue(self) -> List[str]:
        q = []
        for j in os.listdir(self.cfg.jobs_dir):
            if re.fullmatch(r'[0-9a-f]{12}', j):
                st = read_status(os.path.join(self.cfg.jobs_dir, j, 'status.json'))
                if st.get('state') == 'queued' and j not in self._procs:
                    q.append((st.get('created', 0), j))
        return [j for _, j in sorted(q)]

    def _recover(self) -> None:
        for j in os.listdir(self.cfg.jobs_dir):
            sp = os.path.join(self.cfg.jobs_dir, j, 'status.json')
            if read_status(sp).get('state') == 'running':
                write_status(sp, state='failed', finished=time.time(),
                             error='Server restarted while the job was running; submit it again')

    def _tick(self) -> None:
        with self._lock:
            for j, proc in list(self._procs.items()):
                st = check_process(proc, self._status_path(j))
                if st.get('state') in STATES_FINAL:
                    proc.join(timeout=0)
                    self._procs.pop(j, None)
                    logger.info('Job %s %s %s', j, st.get('state'), st.get('error', ''))
            free = self.cfg.workers - len(self._procs)
            for j in self._queue()[:max(0, free)]:
                try:
                    self._procs[j] = start_job_process(self.params(j), self._status_path(j))
                    logger.info('Job %s started', j)
                except Exception as e:
                    write_status(self._status_path(j), state='failed', error=f'Could not start: {e}')

    def _cleanup(self) -> None:
        if self.cfg.keep_days <= 0:
            return
        limit = time.time() - self.cfg.keep_days * 86400
        for row in self.list(limit=100000):
            if row.get('state') in STATES_FINAL and row.get('updated', time.time()) < limit:
                self.delete(row['id'])

    def _loop(self) -> None:
        last_clean = 0.0
        while not self._stop.is_set():
            try:
                self._tick()
                if time.time() - last_clean > 3600:
                    last_clean = time.time()
                    self._cleanup()
            except Exception:
                logger.exception('Job dispatcher error')
            self._stop.wait(0.5)

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        with self._lock:
            for j, proc in list(self._procs.items()):
                cancel_process(proc, self._status_path(j))
                write_status(self._status_path(j), state='failed',
                             error='Server stopped while the job was running; submit it again')
            self._procs.clear()

    def running(self) -> int:
        return len(self._procs)

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Block until the job ends (used by tests)."""
        t0 = time.time()
        while True:
            st = self.status(job_id)
            if st.get('state') in STATES_FINAL:
                return st
            if timeout is not None and time.time() - t0 > timeout:
                return st
            time.sleep(0.3)
