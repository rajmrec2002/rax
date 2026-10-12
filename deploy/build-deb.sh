#!/usr/bin/env bash
# Build Debian / Ubuntu packages (.deb) of Rax Logic.
#
#   bash deploy/build-deb.sh [client|server|both] [--out DIR] [--wheelhouse DIR]
#
#   client  rax-logic-client_<version>_<arch>.deb
#           desktop app (menu: Rax Logic, command: rax-logic) + rax-client,
#           in /opt/rax-logic-client
#   server  rax-logic-server_<version>_<arch>.deb
#           systemd service rax-logic (port 8765, or the next free one),
#           in /opt/rax-logic; accounts: sudo rax-logic-user add --name "..."
#   both    (default) both packages
#
# Install on a PC:   sudo apt install ./rax-logic-client_3.2.0_amd64.deb
# (apt also installs the system libraries it needs; dpkg -i does not.)
#
# The packages contain a ready Python environment with all libraries, so the
# target PC needs no internet.  They are built for this PC's Python version
# and CPU type: build them on the same Ubuntu / Debian release as the PCs
# they are for (e.g. build on 24.04 for 24.04 PCs).
#
# Needs: python3, python3-venv, dpkg-deb; internet, or --wheelhouse DIR made
# with  bash deploy/install.sh bundle DIR.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
OUT="$SRC/dist"
WHAT=both
WHEELHOUSE=""

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "==> $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    client|server|both) WHAT="$1"; shift ;;
    --out) OUT="$2"; shift 2 ;;
    --wheelhouse) WHEELHOUSE="$(realpath "$2")"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see: bash $0 --help)" ;;
  esac
done

command -v dpkg-deb >/dev/null || die "dpkg-deb not found (this builds .deb files on Debian / Ubuntu)"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Python 3.9 or newer is needed"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "python3-venv is missing (sudo apt install python3-venv)"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")"
ARCH="$(dpkg --print-architecture)"
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PYBIN="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
[[ "$PYBIN" == /usr/bin/* ]] || die "use the system Python (/usr/bin/python3), not $PYBIN: the package must run on other PCs"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/rax-deb.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# clean copy of the source (no build files left by an earlier install)
tar -C "$SRC" --exclude=.git --exclude='*.egg-info' --exclude=build --exclude=dist \
    --exclude=__pycache__ --exclude=wheels -cf - . | { mkdir -p "$WORK/src"; tar -C "$WORK/src" -xf -; }

# make_venv <stage root> <install dir, e.g. /opt/rax-logic> <extras>
make_venv() {
  local stage="$1" dir="$2" extras="$3" opts=()
  [[ -n "$WHEELHOUSE" ]] && opts=(--no-index --find-links "$WHEELHOUSE")
  local v="$stage$dir/venv"
  say "Building the Python environment for $dir (Python $PYVER)"
  "$PY" -m venv "$v"
  "$v/bin/python" -m pip install -q --upgrade "${opts[@]}" pip wheel setuptools
  "$v/bin/python" -m pip install -q "${opts[@]}" "$WORK/src[$extras]"
  "$v/bin/python" -m pip uninstall -q -y pip wheel setuptools >/dev/null 2>&1 || true
  # the environment was built in the staging folder: point it at its real place
  grep -rlI --null -F "$stage" "$v/bin" "$v/pyvenv.cfg" | xargs -0 -r sed -i "s#$stage##g"
  find "$v" -name __pycache__ -type d -prune -exec rm -rf {} +     # compiled again on install
  find "$v" -name direct_url.json -delete                           # names the build folder
  rm -f "$v/bin/activate.fish" "$v/bin/activate.csh" "$v/bin/Activate.ps1"
}

# control <stage> <package> <depends> <description lines...>
control() {
  local stage="$1" pkg="$2" deps="$3"; shift 3
  local size; size="$(du -sk --exclude=DEBIAN "$stage" | cut -f1)"
  mkdir -p "$stage/DEBIAN"
  {
    echo "Package: $pkg"
    echo "Version: $VERSION"
    echo "Architecture: $ARCH"
    echo "Maintainer: Rax Development Team <noreply@localhost>"
    echo "Installed-Size: $size"
    echo "Depends: $deps"
    echo "Section: science"
    echo "Priority: optional"
    echo "Description: $1"
    shift
    for l in "$@"; do echo " $l"; done
  } > "$stage/DEBIAN/control"
}

build() {   # build <stage> <file name>
  local f="$OUT/$2"
  if dpkg-deb --help 2>/dev/null | grep -q -- --root-owner-group; then
    dpkg-deb --root-owner-group -Zxz --build "$1" "$f" >/dev/null
  elif command -v fakeroot >/dev/null; then
    fakeroot dpkg-deb -Zxz --build "$1" "$f" >/dev/null
  else
    die "old dpkg-deb: install fakeroot (sudo apt install fakeroot)"
  fi
  say "Built $f ($(du -h "$f" | cut -f1))"
}

# ── client: desktop app + rax-client ─────────────────────────────────────────
build_client() {
  local st="$WORK/client" dir=/opt/rax-logic-client
  make_venv "$st" "$dir" "gui,fast"
  mkdir -p "$st/usr/bin" "$st/usr/share/applications" "$st/usr/share/pixmaps"
  cat > "$st/usr/bin/rax-logic" <<EOF
#!/bin/sh
exec $dir/venv/bin/rax-logic --no-auto-install "\$@"
EOF
  chmod 755 "$st/usr/bin/rax-logic"
  ln -s "$dir/venv/bin/rax-client" "$st/usr/bin/rax-client"
  install -m 644 "$SRC/rax_disp_logic_optimised/gui/assets/app_icon.png" "$st/usr/share/pixmaps/rax-logic.png"
  cat > "$st/usr/share/applications/rax-logic.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Rax Logic – Physical Progress Module
Comment=Locking square sheet, criss-cross and dispensation
Exec=/usr/bin/rax-logic
Icon=rax-logic
Terminal=false
Categories=Office;Engineering;
EOF
  control "$st" rax-logic-client \
    "python$PYVER, libxkbcommon-x11-0, libxcb-cursor0, libegl1, libgl1, libfontconfig1, libdbus-1-3" \
    "Rax Logic desktop app and command-line client" \
    "Locking square sheet, criss-cross and 3.47 / 5.16 dispensation documents" \
    "from a station TOC. Runs jobs on this PC or on a Rax Logic server" \
    "(log in with the login ID and password made by the server)."
  cat > "$st/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
$dir/venv/bin/python -m compileall -q $dir/venv/lib >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
exit 0
EOF
  cat > "$st/DEBIAN/prerm" <<EOF
#!/bin/sh
set -e
find $dir -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
EOF
  chmod 755 "$st/DEBIAN/postinst" "$st/DEBIAN/prerm"
  build "$st" "rax-logic-client_${VERSION}_${ARCH}.deb"
}

# ── server: systemd service ──────────────────────────────────────────────────
build_server() {
  local st="$WORK/server" dir=/opt/rax-logic
  make_venv "$st" "$dir" "server,fast"
  mkdir -p "$st/usr/bin" "$st/usr/sbin" "$st/lib/systemd/system"
  install -m 644 "$SRC/deploy/rax-logic.service" "$st/lib/systemd/system/rax-logic.service"
  ln -s "$dir/venv/bin/rax-server" "$st/usr/bin/rax-server"
  cat > "$st/usr/sbin/rax-logic-user" <<'EOF'
#!/bin/sh
# Manage the server's login accounts (the server makes up ID and password):
#   sudo rax-logic-user add --name "SSE/Sig ADI" [--admin]
#   sudo rax-logic-user list | reset ID | disable ID | enable ID | remove ID
[ "$(id -u)" = 0 ] || { echo "run with sudo: sudo rax-logic-user ${*:-list}" >&2; exit 1; }
DATA=/var/lib/rax-logic
[ -f /etc/rax-logic/server.env ] && D="$(sed -n 's/^RAX_DATA_DIR=//p' /etc/rax-logic/server.env | tail -1)" && [ -n "$D" ] && DATA="$D"
[ $# -gt 0 ] || set -- list
exec runuser -u rax -- /opt/rax-logic/venv/bin/rax-server user --data-dir "$DATA" "$@"
EOF
  chmod 755 "$st/usr/sbin/rax-logic-user"
  control "$st" rax-logic-server "python$PYVER, systemd, passwd" \
    "Rax Logic server (web page + REST API for clients)" \
    "Runs locking, criss-cross and dispensation jobs for the Rax Logic desktop" \
    "app, rax-client and web browsers. Service: rax-logic. Each person logs" \
    "in with a login ID and password made by: sudo rax-logic-user add."
  cat > "$st/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
[ "$1" = configure ] || exit 0
ETC=/etc/rax-logic; DATA=/var/lib/rax-logic
id rax >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin rax
mkdir -p "$DATA" "$ETC"
chown rax:rax "$DATA"; chmod 750 "$DATA"
/opt/rax-logic/venv/bin/python -m compileall -q /opt/rax-logic/venv/lib >/dev/null 2>&1 || true
busy() { ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; }
if [ ! -f "$ETC/server.env" ]; then
  PORT=8765
  while busy "$PORT" && [ "$PORT" -lt 8799 ]; do PORT=$((PORT + 1)); done
  cat > "$ETC/server.env" <<CONF
# Rax Logic server settings (after changes: sudo systemctl restart rax-logic)
RAX_HOST=0.0.0.0
RAX_PORT=$PORT
RAX_DATA_DIR=$DATA
# Jobs that may run at the same time (criss-cross uses all CPU cores per job)
RAX_WORKERS=2
# People log in with accounts made by: sudo rax-logic-user add --name "..."
# Optional server key for scripts (sees all jobs); blank = none
RAX_TOKEN=
# Days a login stays valid on a PC
RAX_SESSION_DAYS=30
RAX_MAX_UPLOAD_MB=50
# Delete finished jobs after this many days (0 = keep)
RAX_KEEP_DAYS=30
CONF
  chown root:rax "$ETC/server.env"; chmod 640 "$ETC/server.env"
fi
PORT="$(sed -n 's/^RAX_PORT=//p' "$ETC/server.env" | tail -1)"
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload || true
  systemctl enable rax-logic >/dev/null 2>&1 || true
  systemctl restart rax-logic || echo "rax-logic did not start: journalctl -u rax-logic -n 50" >&2
fi
FIRST=/root/rax-logic-first-login.txt
if [ ! -s "$DATA/users.json" ]; then
  umask 077
  runuser -u rax -- /opt/rax-logic/venv/bin/rax-server user --data-dir "$DATA" \
      add --admin --name Administrator > "$FIRST" 2>&1 || true
  echo ""; cat "$FIRST"
  echo "(also saved in $FIRST - delete it once noted)"
fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "Rax Logic server: http://${IP:-<server-ip>}:${PORT}/"
echo "  Accounts: sudo rax-logic-user add --name \"SSE/Sig ADI\"   (list, reset, disable, remove)"
echo "  Settings: $ETC/server.env    Logs: journalctl -u rax-logic -f"
exit 0
EOF
  cat > "$st/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ -d /run/systemd/system ] && [ "$1" = remove ]; then
  systemctl disable --now rax-logic >/dev/null 2>&1 || true
fi
find /opt/rax-logic -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
EOF
  cat > "$st/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
[ -d /run/systemd/system ] && systemctl daemon-reload >/dev/null 2>&1 || true
if [ "$1" = purge ]; then
  rm -rf /etc/rax-logic
  echo "Settings removed. Jobs and login accounts are kept in /var/lib/rax-logic (delete by hand if not needed)."
fi
exit 0
EOF
  chmod 755 "$st/DEBIAN/postinst" "$st/DEBIAN/prerm" "$st/DEBIAN/postrm"
  build "$st" "rax-logic-server_${VERSION}_${ARCH}.deb"
}

say "Rax Logic $VERSION, $ARCH, Python $PYVER"
case "$WHAT" in
  client) build_client ;;
  server) build_server ;;
  both) build_client; build_server ;;
esac
cat <<MSG

Install on a PC (Debian / Ubuntu with Python $PYVER, like this one):
  sudo apt install ./rax-logic-client_${VERSION}_${ARCH}.deb      desktop app: menu "Rax Logic" or: rax-logic
  sudo apt install ./rax-logic-server_${VERSION}_${ARCH}.deb      server; shows the first login ID + password
Update: install the newer .deb the same way. Remove: sudo apt remove rax-logic-client (or -server).
MSG
