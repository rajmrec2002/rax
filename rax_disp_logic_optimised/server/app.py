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
    POST   /api/login                       form: uid, password -> session token
    POST   /api/logout
    GET    /api/me                          who is logged in

Login: once the administrator has created user accounts (rax-server user
add), every /api call except health and login needs
'Authorization: Bearer <session token>' (or the X-API-Key header, or
?token= on download links).  Users see only their own jobs.  RAX_TOKEN,
if set, is a server key with access to all jobs (for scripts).
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
from .users import UserError, UserStore

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(os.path.dirname(_HERE), 'gui', 'assets')


def create_app(cfg: Optional[ServerConfig] = None) -> FastAPI:
    cfg = cfg or ServerConfig()
    state = {}
    users = UserStore(cfg.data_dir)

    def login_required() -> bool:
        return bool(cfg.token) or users.has_users()

    @asynccontextmanager
    async def lifespan(_app):
        state['jobs'] = JobManager(cfg)
        logger.info('Rax Logic server %s: data in %s, %d worker(s), login %s',
                    __version__, cfg.data_dir, cfg.workers,
                    'on' if login_required() else 'off')
        yield
        state['jobs'].shutdown()

    app = FastAPI(title='Rax Logic server', version=__version__, lifespan=lifespan)
    if os.path.isdir(_ASSETS):
        app.mount('/assets', StaticFiles(directory=_ASSETS), name='assets')

    def jobs() -> JobManager:
        return state['jobs']

    def _given(request: Request) -> str:
        given = request.headers.get('x-api-key', '')
        h = request.headers.get('authorization', '')
        if h.lower().startswith('bearer '):
            given = h[7:].strip()
        return given or request.query_params.get('token', '')   # for plain download links

    def auth(request: Request) -> dict:
        """Who is calling: {'uid', 'name', 'admin'}.  Admin = all jobs."""
        if not login_required():
            return {'uid': '', 'name': '', 'admin': True}
        given = _given(request)
        if cfg.token and given and secrets.compare_digest(given, cfg.token):
            return {'uid': '', 'name': 'server key', 'admin': True}
        who = users.session(given)
        if who is None:
            raise HTTPException(401, 'Not logged in, or the session has ended: '
                                     'log in with your login ID and password')
        return who

    async def read_upload(f: Optional[UploadFile], what: str):
        if f is None or not f.filename:
            return None
        data = await f.read(cfg.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > cfg.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f'{what} file is larger than {cfg.max_upload_mb} MB')
        if not data:
            raise HTTPException(400, f'{what} file is empty')
        return (f.filename, data)

    def _get(job_id: str, who: dict):
        try:
            st = jobs().status(job_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, 'No such job')
        if not who['admin'] and st.get('user', '') != who['uid']:
            raise HTTPException(404, 'No such job')     # another user's job
        return st

    # ── web page ─────────────────────────────────────────────────────────
    @app.get('/', response_class=HTMLResponse, include_in_schema=False)
    def index():
        with open(os.path.join(_HERE, 'static', 'index.html'), encoding='utf-8') as f:
            return f.read().replace('{{VERSION}}', __version__) \
                           .replace('{{LOGIN}}', 'true' if login_required() else 'false')

    # ── API ──────────────────────────────────────────────────────────────
    @app.get('/api/health')
    def health():
        return {'app': 'rax-logic', 'version': __version__,
                'running': jobs().running(), 'workers': cfg.workers,
                'login_required': login_required()}

    @app.post('/api/login')
    def login(uid: str = Form(...), password: str = Form(...)):
        if not users.has_users():
            raise HTTPException(400, 'This server has no user accounts '
                                     '(the administrator creates them: rax-server user add)')
        try:
            s = users.login(uid, password)
        except UserError as e:
            code = 429 if 'Too many' in str(e) else 401
            raise HTTPException(code, str(e))
        logger.info('Login: %s', s['uid'])
        return s

    @app.post('/api/logout')
    def logout(request: Request):
        users.logout(_given(request))
        return {'ok': True}

    @app.get('/api/me')
    def me(who: dict = Depends(auth)):
        return who

    @app.post('/api/jobs', status_code=201)
    async def submit(
        who: dict = Depends(auth),
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
        job_id = await run_in_threadpool(jobs().create, params, files, who['uid'])
        return _get(job_id, who)

    @app.get('/api/jobs')
    def list_jobs(limit: int = 100, who: dict = Depends(auth)):
        return jobs().list(limit, None if who['admin'] else who['uid'])

    @app.get('/api/jobs/{job_id}')
    def job_status(job_id: str, who: dict = Depends(auth)):
        return _get(job_id, who)

    @app.get('/api/jobs/{job_id}/files/{name}')
    def job_file(job_id: str, name: str, who: dict = Depends(auth)):
        _get(job_id, who)
        try:
            path = jobs().output_file(job_id, name)
        except KeyError:
            raise HTTPException(404, 'No such file')
        return FileResponse(path, filename=os.path.basename(path))

    @app.get('/api/jobs/{job_id}/download')
    def job_zip(job_id: str, who: dict = Depends(auth)):
        st = _get(job_id, who)
        path = jobs().zip_outputs(job_id)
        return FileResponse(path, media_type='application/zip',
                            filename=f'{safe_name(st.get("stn", "STN"))}_{job_id}.zip')

    @app.post('/api/jobs/{job_id}/cancel')
    def job_cancel(job_id: str, who: dict = Depends(auth)):
        _get(job_id, who)
        return jobs().cancel(job_id)

    @app.delete('/api/jobs/{job_id}', status_code=204)
    def job_delete(job_id: str, who: dict = Depends(auth)):
        _get(job_id, who)
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
