"""
Server settings.

Read from command-line options, else environment variables (the systemd
unit loads them from /etc/rax-logic/server.env), else defaults.
"""

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class ServerConfig:
    host: str = _env('RAX_HOST', '0.0.0.0')
    port: int = int(_env('RAX_PORT', '8080'))
    data_dir: str = _env('RAX_DATA_DIR', os.path.expanduser('~/.rax-logic/server'))
    workers: int = int(_env('RAX_WORKERS', '2'))           # jobs run at the same time
    token: str = _env('RAX_TOKEN', '')                     # '' = no login needed
    max_upload_mb: int = int(_env('RAX_MAX_UPLOAD_MB', '50'))
    keep_days: int = int(_env('RAX_KEEP_DAYS', '30'))      # 0 = keep jobs for ever

    @property
    def jobs_dir(self) -> str:
        return os.path.join(self.data_dir, 'jobs')
