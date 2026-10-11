"""
Talk to a Rax Logic server.  Standard library only, so the client
install needs nothing beyond Python (pandas only for the desktop app).

    c = RaxClient('http://server:8765', token='...')
    job = c.submit('TOC.xlsx', stn='ADI', hs=['S5,CO5', ...], end=['VIRAR', ...],
                   l_no=18, criss_cross=True, chainage='CH.xlsx', bands='0-120, 300+')
    job = c.wait(job['id'], on_progress=print)
    c.download(job['id'], 'out/')            # unpacks all outputs
"""

import io
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from typing import Callable, Dict, List, Optional, Sequence

CONFIG_PATH = os.path.expanduser('~/.rax-logic/client.conf')
FINAL = ('done', 'failed', 'cancelled')


class ServerError(Exception):
    pass


def load_client_config() -> Dict[str, str]:
    """SERVER_URL / TOKEN from ~/.rax-logic/client.conf, overridden by $RAX_SERVER / $RAX_TOKEN."""
    cfg: Dict[str, str] = {}
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding='utf-8') as f:
            for line in f:
                if '=' in line and not line.lstrip().startswith('#'):
                    k, _, v = line.partition('=')
                    cfg[k.strip().upper()] = v.strip()
    if os.environ.get('RAX_SERVER'):
        cfg['SERVER_URL'] = os.environ['RAX_SERVER']
    if os.environ.get('RAX_TOKEN'):
        cfg['TOKEN'] = os.environ['RAX_TOKEN']
    return cfg


def save_client_config(server_url: str, token: str = '') -> str:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(f'SERVER_URL={server_url}\nTOKEN={token}\n')
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return CONFIG_PATH


def _multipart(fields: Dict[str, str], files: Dict[str, str]):
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                  .encode())
        buf.write(str(v).encode('utf-8'))
        buf.write(b'\r\n')
    for k, path in files.items():
        name = os.path.basename(path).replace('"', '_')
        ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                  f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode('utf-8'))
        with open(path, 'rb') as f:
            buf.write(f.read())
        buf.write(b'\r\n')
    buf.write(f'--{boundary}--\r\n'.encode())
    return buf.getvalue(), f'multipart/form-data; boundary={boundary}'


class RaxClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 60):
        cfg = load_client_config()
        base_url = base_url or cfg.get('SERVER_URL', '')
        if not base_url:
            raise ServerError('No server address. Use --server http://host:8765, '
                              'set RAX_SERVER, or run:  rax-client config --server URL')
        if '://' not in base_url:
            base_url = 'http://' + base_url
        self.base = base_url.rstrip('/')
        self.token = token if token is not None else cfg.get('TOKEN', '')
        self.timeout = timeout

    # ── low level ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, data: bytes = None,
                 ctype: str = None, raw: bool = False):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        if ctype:
            req.add_header('Content-Type', ctype)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            detail = e.reason
            try:
                d = json.loads(e.read().decode('utf-8')).get('detail', detail)
                detail = d if isinstance(d, str) else json.dumps(d)
            except Exception:
                pass
            raise ServerError(f'Server said: {detail} (HTTP {e.code})') from None
        except urllib.error.URLError as e:
            raise ServerError(f'Cannot reach the server at {self.base}: {e.reason}') from None
        if raw:
            return body
        return json.loads(body.decode('utf-8')) if body else None

    # ── API ──────────────────────────────────────────────────────────────
    def health(self) -> dict:
        return self._request('GET', '/api/health')

    def submit(self, toc: str, stn: str, hs: Sequence[str] = ('',) * 4,
               end: Sequence[str] = ('',) * 4, l_no: int = 1, *,
               criss_cross: bool = False, chainage: str = '', vv: str = '',
               bands: str = '', cc_min: float = 0, cc_max: float = 0,
               extra_chainage: Optional[Dict[str, float]] = None,
               iso_preferred: bool = True) -> dict:
        hs = list(hs) + [''] * (4 - len(hs))
        end = list(end) + [''] * (4 - len(end))
        fields = {'stn': stn, 'berthing_lines': int(l_no),
                  'criss_cross': 'true' if criss_cross else 'false',
                  'cc_bands': bands or '', 'cc_min': cc_min, 'cc_max': cc_max,
                  'iso_preferred': 'true' if iso_preferred else 'false',
                  'extra_chainage': '\n'.join(f'{k}, {v}' for k, v in (extra_chainage or {}).items())}
        for i, g in enumerate('abcd'):
            fields[f'hs_{g}'] = hs[i] or ''
            fields[f'end_{g}'] = end[i] or ''
        files = {'toc': toc}
        if chainage:
            files['chainage'] = chainage
        if vv:
            files['vv'] = vv
        for k, p in files.items():
            if not os.path.isfile(p):
                raise ServerError(f'{k} file not found: {p}')
        body, ctype = _multipart(fields, files)
        return self._request('POST', '/api/jobs', body, ctype)

    def status(self, job_id: str) -> dict:
        return self._request('GET', f'/api/jobs/{urllib.parse.quote(job_id)}')

    def jobs(self, limit: int = 50) -> List[dict]:
        return self._request('GET', f'/api/jobs?limit={int(limit)}')

    def cancel(self, job_id: str) -> dict:
        return self._request('POST', f'/api/jobs/{urllib.parse.quote(job_id)}/cancel')

    def delete(self, job_id: str) -> None:
        self._request('DELETE', f'/api/jobs/{urllib.parse.quote(job_id)}')

    def wait(self, job_id: str, on_progress: Optional[Callable[[dict], None]] = None,
             poll: float = 2.0, timeout: Optional[float] = None) -> dict:
        t0 = time.time()
        while True:
            st = self.status(job_id)
            if on_progress:
                on_progress(st)
            if st.get('state') in FINAL:
                return st
            if timeout and time.time() - t0 > timeout:
                raise ServerError(f'Job {job_id} still {st.get("state")} after {timeout:.0f}s')
            time.sleep(poll)

    def download(self, job_id: str, dest_dir: str) -> List[str]:
        """Download all outputs and unpack them into dest_dir. Returns the file paths."""
        data = self._request('GET', f'/api/jobs/{urllib.parse.quote(job_id)}/download', raw=True)
        os.makedirs(dest_dir, exist_ok=True)
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                if not base:
                    continue
                path = os.path.join(dest_dir, base)
                with z.open(name) as src, open(path, 'wb') as dst:
                    dst.write(src.read())
                out.append(path)
        return out

    def check_chainage(self, toc: str, chainage: str = '',
                       extra_chainage: Optional[Dict[str, float]] = None) -> List[str]:
        files = {'toc': toc}
        if chainage:
            files['chainage'] = chainage
        body, ctype = _multipart(
            {'extra_chainage': '\n'.join(f'{k}, {v}' for k, v in (extra_chainage or {}).items())},
            files)
        return self._request('POST', '/api/check-chainage', body, ctype)['missing']

    def detect_home_signals(self, toc: str) -> List[dict]:
        body, ctype = _multipart({}, {'toc': toc})
        return self._request('POST', '/api/detect-home-signals', body, ctype)['groups']
