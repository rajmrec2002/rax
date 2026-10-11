#!/usr/bin/env bash
# Rax Logic installer for Linux.
#
#   Server (needs root; runs as a systemd service):
#     sudo bash deploy/install.sh server [--port 8765] [--workers 2] [--token TOKEN | --no-token]
#                                     [--bind 0.0.0.0] [--wheelhouse DIR]
#
#   Client (normal user; no root):
#     bash deploy/install.sh client [--cli-only] [--server http://host:8765] [--token TOKEN]
#                                [--wheelhouse DIR]
#       default: desktop app (PySide6) + rax-client command
#       --cli-only: rax-client command only (no pandas / PySide6)
#
#   Offline installs: on a PC with internet run
#     bash deploy/install.sh bundle DIR        (downloads all wheels into DIR)
#   copy the repository and DIR across, then add  --wheelhouse DIR.
#
#   Update to the latest version (pulls the code, reinstalls, restarts;
#   settings and jobs are kept):
#     sudo bash deploy/install.sh update        (server)
#     bash deploy/install.sh update             (client, on each PC)
#     add --no-pull to use the files as they are (e.g. copied from a USB stick)
#
#   Remove:
#     sudo bash deploy/install.sh uninstall-server [--purge]   (--purge also deletes jobs)
#     bash deploy/install.sh uninstall-client
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
PREFIX=/opt/rax-logic
DATA=/var/lib/rax-logic
ETC=/etc/rax-logic
UNIT=/etc/systemd/system/rax-logic.service
CLIENT_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/rax-logic"
BIN="$HOME/.local/bin"

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "==> $*"; }

need_python() {
  command -v "$PY" >/dev/null || die "python3 not found. Install it first (e.g. sudo apt install python3 python3-venv)."
  "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Python 3.9 or newer is needed."
  "$PY" -c 'import venv, ensurepip' 2>/dev/null || die "python3-venv is missing (Debian/Ubuntu: sudo apt install python3-venv)."
}

pip_install() {   # venv_dir  spec...
  local venv="$1"; shift
  local opts=()
  [[ -n "${WHEELHOUSE:-}" ]] && opts=(--no-index --find-links "$WHEELHOUSE")
  "$venv/bin/python" -m pip install --upgrade "${opts[@]}" pip wheel setuptools >/dev/null
  "$venv/bin/python" -m pip install --upgrade "${opts[@]}" "$@"
  # always replace the Rax Logic code itself, even if the version number is unchanged
  "$venv/bin/python" -m pip install --force-reinstall --no-deps "${opts[@]}" "$SRC" >/dev/null
}

git_pull() {      # bring the source folder up to date (as the folder's owner)
  [[ "${NO_PULL:-0}" == 1 ]] && return 0
  if [[ -d "$SRC/.git" ]] && command -v git >/dev/null; then
    say "Updating source in $SRC"
    local owner; owner="$(stat -c %U "$SRC")"
    if [[ $EUID -eq 0 && "$owner" != root ]]; then
      runuser -u "$owner" -- git -C "$SRC" pull --ff-only || die "git pull failed (see above)"
    else
      git -C "$SRC" pull --ff-only || die "git pull failed (see above)"
    fi
  else
    say "Not a git checkout: using the files in $SRC as they are"
  fi
}

update_server() {
  say "Updating server in $PREFIX/venv"
  pip_install "$PREFIX/venv" "$SRC[server,fast]"
  if command -v systemctl >/dev/null; then
    install -m 644 "$SRC/deploy/rax-logic.service" "$UNIT"
    systemctl daemon-reload
    systemctl restart rax-logic
    sleep 2
    systemctl is-active --quiet rax-logic && say "Server restarted." \
      || echo "Service did not start; see: journalctl -u rax-logic -n 50" >&2
  fi
  echo "  Server version: $("$PREFIX/venv/bin/rax-server" --version)"
  echo "  Settings kept: $ETC/server.env    Jobs kept: $DATA/jobs"
}

update_client() {
  say "Updating client in $CLIENT_HOME/venv"
  if "$CLIENT_HOME/venv/bin/python" -c 'import PySide6' 2>/dev/null; then   # desktop app installed
    pip_install "$CLIENT_HOME/venv" "$SRC[gui,fast]"
    cp "$SRC/rax_disp_logic_optimised/gui/assets/app_icon.png" "$CLIENT_HOME/app_icon.png"
  else
    pip_install "$CLIENT_HOME/venv" --no-deps "$SRC"
  fi
  echo "  Client version: $("$CLIENT_HOME/venv/bin/python" -c 'import rax_disp_logic_optimised as r; print(r.__version__)')"
}

update() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-pull) NO_PULL=1; shift ;;
      --wheelhouse) WHEELHOUSE="$(realpath "$2")"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  local server=0 client=0
  [[ $EUID -eq 0 && -x "$PREFIX/venv/bin/python" ]] && server=1
  [[ -x "$CLIENT_HOME/venv/bin/python" ]] && client=1
  if [[ $server == 0 && $client == 0 ]]; then
    [[ -x "$PREFIX/venv/bin/python" ]] && die "the server is installed: run with sudo"
    die "nothing installed here (install with: sudo bash $0 server  or  bash $0 client)"
  fi
  git_pull
  need_python
  [[ $server == 1 ]] && update_server
  [[ $client == 1 ]] && update_client
  say "Update done."
}

port_busy() {      # port -> 0 if something is listening on it
  if command -v ss >/dev/null; then
    ss -Hltn "sport = :$1" 2>/dev/null | grep -q .
  else
    "$PY" - "$1" <<'PYEOF'
import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('0.0.0.0', int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
PYEOF
  fi
}

port_owner() {     # show which program uses the port (best effort)
  if command -v ss >/dev/null; then ss -Hltnp "sport = :$1" 2>/dev/null | sed 's/^/    /'; fi
}

free_port() {      # first free port from 8765 up
  local p
  for p in $(seq 8765 8799); do port_busy "$p" || { echo "$p"; return; }; done
  echo 9765
}

# ── server ───────────────────────────────────────────────────────────────────
install_server() {
  local port="" workers=2 bind=0.0.0.0 token="" no_token=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port) port="$2"; shift 2 ;;
      --workers) workers="$2"; shift 2 ;;
      --bind) bind="$2"; shift 2 ;;
      --token) token="$2"; shift 2 ;;
      --no-token) no_token=1; shift ;;
      --wheelhouse) WHEELHOUSE="$(realpath "$2")"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  [[ $EUID -eq 0 ]] || die "server install needs root: sudo $0 server ..."
  command -v systemctl >/dev/null || die "systemd not found."
  need_python

  # Port: --port, else the one already configured, else 8765.  It must not be
  # taken by another program (e.g. another dashboard on 8080).
  local configured=""
  [[ -f "$ETC/server.env" ]] && configured="$(sed -n 's/^RAX_PORT=//p' "$ETC/server.env" | tail -1)"
  port="${port:-${configured:-8765}}"
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || die "--port must be a number 1-65535"
  systemctl stop rax-logic 2>/dev/null || true      # so our own old instance does not count as "busy"
  if port_busy "$port"; then
    echo "ERROR: port $port is already used by another program:" >&2
    port_owner "$port" >&2
    echo "Choose a free port, e.g.:  sudo bash $0 server --port $(free_port)" >&2
    exit 1
  fi

  say "Creating user 'rax' and folders"
  id rax >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin rax
  mkdir -p "$PREFIX" "$DATA" "$ETC"
  chown rax:rax "$DATA"; chmod 750 "$DATA"

  say "Installing into $PREFIX/venv"
  [[ -x "$PREFIX/venv/bin/python" ]] || "$PY" -m venv "$PREFIX/venv"
  pip_install "$PREFIX/venv" "$SRC[server,fast]"

  if [[ -f "$ETC/server.env" ]]; then
    say "Keeping existing $ETC/server.env (port set to $port)"
    if grep -q '^RAX_PORT=' "$ETC/server.env"; then
      sed -i "s/^RAX_PORT=.*/RAX_PORT=$port/" "$ETC/server.env"
    else
      echo "RAX_PORT=$port" >> "$ETC/server.env"
    fi
  else
    if [[ $no_token -eq 0 && -z "$token" ]]; then
      token="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(24))')"
    fi
    say "Writing $ETC/server.env"
    cat > "$ETC/server.env" <<CONF
# Rax Logic server settings (restart after changes: sudo systemctl restart rax-logic)
RAX_HOST=$bind
RAX_PORT=$port
RAX_DATA_DIR=$DATA
# Jobs that may run at the same time (criss-cross uses all CPU cores per job)
RAX_WORKERS=$workers
# Login token clients must send; blank = no login (only on a trusted network)
RAX_TOKEN=$token
RAX_MAX_UPLOAD_MB=50
# Delete finished jobs after this many days (0 = keep)
RAX_KEEP_DAYS=30
CONF
    chown root:rax "$ETC/server.env"; chmod 640 "$ETC/server.env"
  fi

  say "Installing systemd service"
  install -m 644 "$SRC/deploy/rax-logic.service" "$UNIT"
  systemctl daemon-reload
  systemctl enable rax-logic >/dev/null
  systemctl restart rax-logic

  # shellcheck disable=SC1091
  . "$ETC/server.env"
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  sleep 2
  if systemctl is-active --quiet rax-logic; then
    say "Server running."
  else
    echo "Service did not start; see: journalctl -u rax-logic -n 50" >&2
  fi
  cat <<MSG

  Web page:  http://${ip:-<server-ip>}:${RAX_PORT}/
  Login token: ${RAX_TOKEN:-<none – anyone on the network can use it>}
  Settings:  $ETC/server.env      Jobs: $DATA/jobs
  Logs:      journalctl -u rax-logic -f
  Firewall:  open TCP ${RAX_PORT} if needed (e.g. sudo ufw allow ${RAX_PORT}/tcp)

  On each client PC:
    bash deploy/install.sh client --server http://${ip:-<server-ip>}:${RAX_PORT} --token '${RAX_TOKEN}'
MSG
}

uninstall_server() {
  [[ $EUID -eq 0 ]] || die "needs root"
  systemctl disable --now rax-logic 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  rm -rf "$PREFIX"
  if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$DATA" "$ETC"; userdel rax 2>/dev/null || true
    say "Server removed, jobs and settings deleted."
  else
    say "Server removed. Jobs kept in $DATA, settings in $ETC (use --purge to delete)."
  fi
}

# ── client ───────────────────────────────────────────────────────────────────
install_client() {
  local cli_only=0 server="" token=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cli-only) cli_only=1; shift ;;
      --server) server="$2"; shift 2 ;;
      --token) token="$2"; shift 2 ;;
      --wheelhouse) WHEELHOUSE="$(realpath "$2")"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  need_python
  say "Installing into $CLIENT_HOME/venv"
  mkdir -p "$CLIENT_HOME" "$BIN"
  [[ -x "$CLIENT_HOME/venv/bin/python" ]] || "$PY" -m venv "$CLIENT_HOME/venv"
  if [[ $cli_only -eq 1 ]]; then
    # rax-client uses only the standard library
    pip_install "$CLIENT_HOME/venv" --no-deps "$SRC"
  else
    pip_install "$CLIENT_HOME/venv" "$SRC[gui,fast]"
  fi
  ln -sf "$CLIENT_HOME/venv/bin/rax-client" "$BIN/rax-client"

  if [[ $cli_only -eq 0 ]]; then
    ln -sf "$CLIENT_HOME/venv/bin/rax-logic" "$BIN/rax-logic"
    local icon="$CLIENT_HOME/app_icon.png"
    cp "$SRC/rax_disp_logic_optimised/gui/assets/app_icon.png" "$icon"
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/rax-logic.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Rax Logic – Physical Progress Module
Comment=Locking square sheet, criss-cross and dispensation
Exec=$CLIENT_HOME/venv/bin/rax-logic --no-auto-install
Icon=$icon
Terminal=false
Categories=Office;Engineering;
DESK
    command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    # Qt needs these system libraries on a minimal desktop
    if ! ldconfig -p 2>/dev/null | grep -q libxkbcommon-x11; then
      echo "Note: if the window does not open, install: sudo apt install libxkbcommon-x11-0 libxcb-cursor0 libegl1"
    fi
  fi

  if [[ -n "$server" ]]; then
    "$CLIENT_HOME/venv/bin/rax-client" config --server "$server" --token "$token" \
      || echo "Saved, but the server did not answer yet." >&2
  fi
  case ":$PATH:" in *":$BIN:"*) ;; *) echo "Add $BIN to PATH:  echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc" ;; esac
  say "Client installed."
  [[ $cli_only -eq 0 ]] && echo "  Desktop app: 'Rax Logic' in the applications menu, or run: rax-logic"
  echo "  Command line: rax-client --help"
  [[ -n "$server" ]] && echo "  Server: $server (in the desktop app put it in 'Run on server', or leave blank to run locally)"
  return 0
}

uninstall_client() {
  rm -rf "$CLIENT_HOME"
  rm -f "$BIN/rax-client" "$BIN/rax-logic" "$HOME/.local/share/applications/rax-logic.desktop"
  say "Client removed (settings in ~/.rax-logic kept)."
}

bundle() {
  local dir="${1:?usage: $0 bundle DIR}"
  need_python
  mkdir -p "$dir"
  "$PY" -m pip download --dest "$dir" pip wheel setuptools "$SRC[server,gui,fast]"
  "$PY" -m pip wheel --no-deps --wheel-dir "$dir" "$SRC"
  say "Wheels saved in $dir (same Python version and CPU type as the target PCs)."
}

case "${1:-}" in
  server) shift; install_server "$@" ;;
  client) shift; install_client "$@" ;;
  uninstall-server) shift; uninstall_server "$@" ;;
  uninstall-client) shift; uninstall_client ;;
  bundle) shift; bundle "$@" ;;
  update) shift; update "$@" ;;
  *) sed -n '2,26p' "$0"; exit 1 ;;
esac
