"""
FastAPI application: web page at /, REST API under /api.

    GET    /api/health                      version, running / queued jobs
    POST   /api/jobs                        multipart: toc, chainage?, vv?, + form fields
    GET    /api/jobs                        recent jobs
    GET    /api/jobs/{id}                   state, progress, files, missing chainage
    GET    /api/jobs/{id}/files/{name}      one output file
    GET    /api/jobs/{id}/download          all outputs as a zip
    POST   /api/jobs/{id}/cancel
    DELETE /api/jobs/{id}
    POST   /api/check-chainage              multipart: toc, chainage? -> missing names
    POST   /api/detect-home-signals         multipart: toc -> detected groups

If a token is configured every /api call needs
'Authorization: Bearer <token>' (or the X-API-Key header).
"""

import logging
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .config import ServerConfig
from .manager import JobManager, safe_name

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(os.path.dirname(_HERE), 'gui', 'assets')


def create_app(cfg: Optional[ServerConfig] = None) -> FastAPI:
    cfg = cfg or ServerConfig()
    state = {}

    @asynccontextmanager
    async def lifespan(_app):
        state['jobs'] = JobManager(cfg)
        logger.info('Rax Logic server %s: data in %s, %d worker(s), login %s',
                    __version__, cfg.data_dir, cfg.workers, 'on' if cfg.token else 'off')
        yield
        state['jobs'].shutdown()

    app = FastAPI(title='Rax Logic server', version=__version__, lifespan=lifespan)
    if os.path.isdir(_ASSETS):
        app.mount('/assets', StaticFiles(directory=_ASSETS), name='assets')

    def jobs() -> JobManager:
        return state['jobs']

    def auth(request: Request) -> None:
        if not cfg.token:
            return
        given = request.headers.get('x-api-key', '')
        h = request.headers.get('authorization', '')
        if h.lower().startswith('bearer '):
            given = h[7:].strip()
        if not given:
            given = request.query_params.get('token', '')   # for plain download links
        if not secrets.compare_digest(given, cfg.token):
            raise HTTPException(401, 'Login token missing or wrong')

    async def read_upload(f: Optional[UploadFile], what: str):
        if f is None or not f.filename:
            return None
        data = await f.read(cfg.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > cfg.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f'{what} file is larger than {cfg.max_upload_mb} MB')
        if not data:
            raise HTTPException(400, f'{what} file is empty')
        return (f.filename, data)

    def _get(job_id: str):
        try:
            return jobs().status(job_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, 'No such job')

    # ── web page ─────────────────────────────────────────────────────────
    @app.get('/', response_class=HTMLResponse, include_in_schema=False)
    def index():
        with open(os.path.join(_HERE, 'static', 'index.html'), encoding='utf-8') as f:
            return f.read().replace('{{VERSION}}', __version__) \
                           .replace('{{LOGIN}}', 'true' if cfg.token else 'false')

    # ── API ──────────────────────────────────────────────────────────────
    @app.get('/api/health')
    def health():
        return {'app': 'rax-logic', 'version': __version__,
                'running': jobs().running(), 'workers': cfg.workers,
                'login_required': bool(cfg.token)}

    @app.post('/api/jobs', dependencies=[Depends(auth)], status_code=201)
    async def submit(
        toc: UploadFile = File(...),
        chainage: Optional[UploadFile] = File(None),
        vv: Optional[UploadFile] = File(None),
        stn: str = Form(...),
        hs_a: str = Form(''), end_a: str = Form(''),
        hs_b: str = Form(''), end_b: str = Form(''),
        hs_c: str = Form(''), end_c: str = Form(''),
        hs_d: str = Form(''), end_d: str = Form(''),
        berthing_lines: int = Form(1),
        criss_cross: bool = Form(False),
        cc_bands: str = Form(''),
        cc_min: float = Form(0), cc_max: float = Form(0),
        extra_chainage: str = Form(''),
        iso_preferred: bool = Form(True),
        iso_distance: str = Form('120'),
        iso_rear_end: bool = Form(False),
    ):
        from ..processing.pipeline import parse_bands, parse_chainage_text
        try:
            bands = parse_bands(cc_bands) if cc_bands.strip() else (
                [(cc_min, cc_max)] if criss_cross else [])
            extra = parse_chainage_text(extra_chainage)
            t = iso_distance.strip().lower()
            iso_dist = None if t in ('', 'none', 'no limit') else float(t)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if criss_cross:
            if not bands or any(hi <= lo for lo, hi in bands):
                raise HTTPException(422, 'Criss-cross needs distance bands, '
                                         'or a maximum distance above the minimum')
        files = {'toc': await read_upload(toc, 'TOC'),
                 'chainage': await read_upload(chainage, 'Chainage'),
                 'vv': await read_upload(vv, 'VV square sheet')}
        if not files['toc']:
            raise HTTPException(400, 'TOC file is required')
        params = {
            'stn': stn.strip(),
            'hs': [hs_a, hs_b, hs_c, hs_d], 'end': [end_a, end_b, end_c, end_d],
            'l_no': berthing_lines, 'criss_cross': criss_cross,
            'bands': [[lo, None if hi == float('inf') else hi] for lo, hi in bands],
            'extra_chainage': extra,
            'iso_preferred': iso_preferred,
            'iso_distance': iso_dist,
            'iso_rear_end': iso_rear_end,
        }
        job_id = await run_in_threadpool(jobs().create, params, files)
        return _get(job_id)

    @app.get('/api/jobs', dependencies=[Depends(auth)])
    def list_jobs(limit: int = 100):
        return jobs().list(limit)

    @app.get('/api/jobs/{job_id}', dependencies=[Depends(auth)])
    def job_status(job_id: str):
        return _get(job_id)

    @app.get('/api/jobs/{job_id}/files/{name}', dependencies=[Depends(auth)])
    def job_file(job_id: str, name: str):
        _get(job_id)
        try:
            path = jobs().output_file(job_id, name)
        except KeyError:
            raise HTTPException(404, 'No such file')
        return FileResponse(path, filename=os.path.basename(path))

    @app.get('/api/jobs/{job_id}/download', dependencies=[Depends(auth)])
    def job_zip(job_id: str):
        st = _get(job_id)
        path = jobs().zip_outputs(job_id)
        return FileResponse(path, media_type='application/zip',
                            filename=f'{safe_name(st.get("stn", "STN"))}_{job_id}.zip')

    @app.post('/api/jobs/{job_id}/cancel', dependencies=[Depends(auth)])
    def job_cancel(job_id: str):
        _get(job_id)
        return jobs().cancel(job_id)

    @app.delete('/api/jobs/{job_id}', dependencies=[Depends(auth)], status_code=204)
    def job_delete(job_id: str):
        _get(job_id)
        jobs().delete(job_id)

    def _with_temp_files(files, fn):
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for key, val in files.items():
                if val:
                    paths[key] = os.path.join(d, f'{key}_{safe_name(val[0], key)}')
                    with open(paths[key], 'wb') as f:
                        f.write(val[1])
            return fn(paths)

    @app.post('/api/check-chainage', dependencies=[Depends(auth)])
    async def check_chainage(toc: UploadFile = File(...),
                             chainage: Optional[UploadFile] = File(None),
                             extra_chainage: str = Form('')):
        from ..processing.pipeline import check_chainage as _check, parse_chainage_text
        files = {'toc': await read_upload(toc, 'TOC'),
                 'chainage': await read_upload(chainage, 'Chainage')}
        try:
            extra = parse_chainage_text(extra_chainage)
            missing = await run_in_threadpool(_with_temp_files, files, lambda p: _check(
                p['toc'], p.get('chainage', ''), extra))
        except ValueError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            raise HTTPException(400, f'Could not read the files: {type(e).__name__}: {e}')
        return {'missing': missing, 'chainage_file': bool(files['chainage'])}

    @app.post('/api/detect-home-signals', dependencies=[Depends(auth)])
    async def detect_home_signals(toc: UploadFile = File(...)):
        from ..processing.auto_detect import auto_detect_home_signals
        from ..processing.pipeline import read_toc_preview
        files = {'toc': await read_upload(toc, 'TOC')}

        def run(p):
            hs, end = auto_detect_home_signals(read_toc_preview(p['toc']))
            return [{'signals': ','.join(s.rstrip('_') for s in g if s.strip('_')), 'end': e}
                    for g, e in zip(hs, end)]
        try:
            groups = await run_in_threadpool(_with_temp_files, files, run)
        except Exception as e:
            raise HTTPException(400, f'Could not read the TOC: {type(e).__name__}: {e}')
        return {'groups': groups}

    return app
