#!/usr/bin/env bash
# Rax Logic – one-go interactive setup for Linux.
#
#   bash setup.sh
#
# Asks what to do (install / update / remove, server / client …), checks
# and installs what is missing (python3, venv, git), shows a summary to
# confirm, then runs deploy/install.sh.  Press Enter to take the default
# shown in [brackets].
set -uo pipefail

USER="${USER:-$(id -un)}"
REPO_URL_DEFAULT="https://github.com/rajmrec2002/rax.git"
BRANCH_DEFAULT="claude/beautiful-volta-awn2nc"

# ── terminal helpers ─────────────────────────────────────────────────────────
if [[ -r /dev/tty && -t 1 ]]; then IN=/dev/tty; else IN=/dev/stdin; fi
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B= G= Y= R= N=; fi

say()  { echo "${G}==>${N} $*"; }
warn() { echo "${Y}!!${N} $*"; }
die()  { echo "${R}ERROR:${N} $*" >&2; exit 1; }
hr()   { echo "────────────────────────────────────────────────────────────"; }

# ask "question" default  -> answer in $REPLY
ask() {
  local q="$1" def="${2:-}"
  read -r -p "${B}$q${N}${def:+ [$def]}: " REPLY < "$IN" || REPLY=""
  REPLY="${REPLY:-$def}"
}

# choose "question" "a=meaning" "b=meaning" ... (first is default) -> key in $REPLY
choose() {
  local q="$1"; shift
  local keys=() def
  for o in "$@"; do keys+=("${o%%=*}"); done
  def="${keys[0]}"
  while true; do
    echo "${B}$q${N}"
    for o in "$@"; do echo "   ${o%%=*}) ${o#*=}"; done
    read -r -p "   choice [$def]: " REPLY < "$IN" || REPLY=""
    REPLY="$(echo "${REPLY:-$def}" | tr '[:upper:]' '[:lower:]')"
    for k in "${keys[@]}"; do [[ "$REPLY" == "$k" ]] && return 0; done
    warn "please type one of: ${keys[*]}"
  done
}

# yesno "question" y|n -> 0 for yes
yesno() {
  local q="$1" def="${2:-y}" a
  while true; do
    read -r -p "${B}$q${N} (y/n) [$def]: " a < "$IN" || a=""
    a="$(echo "${a:-$def}" | tr '[:upper:]' '[:lower:]')"
    case "$a" in y|yes) return 0 ;; n|no) return 1 ;; esac
    warn "please answer y or n"
  done
}

SUDO=""
need_root() {   # run privileged commands with sudo when not root
  if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null || die "this needs root: run as root or install sudo"
    SUDO="sudo"
  fi
}

port_busy() {
  if command -v ss >/dev/null; then ss -Hltn "sport = :$1" 2>/dev/null | grep -q .
  else python3 -c "import socket,sys;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: s.bind(('0.0.0.0',int(sys.argv[1])))
except OSError: sys.exit(0)
sys.exit(1)" "$1"; fi
}
free_port() { local p; for p in $(seq "${1:-8765}" 8799); do port_busy "$p" || { echo "$p"; return; }; done; echo 9765; }

# ── prerequisites ────────────────────────────────────────────────────────────
pkg_install() {   # pkg_install <debian names> -- <rhel names>
  local deb=() rpm=() side=deb
  for a in "$@"; do [[ "$a" == "--" ]] && { side=rpm; continue; }; [[ $side == deb ]] && deb+=("$a") || rpm+=("$a"); done
  need_root
  if command -v apt-get >/dev/null; then $SUDO apt-get update -qq && $SUDO apt-get install -y "${deb[@]}"
  elif command -v dnf >/dev/null; then $SUDO dnf install -y "${rpm[@]}"
  elif command -v yum >/dev/null; then $SUDO yum install -y "${rpm[@]}"
  else die "cannot install ${deb[*]} automatically: install it by hand and run again"; fi
}

check_prereqs() {
  local want_gui="$1"
  if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    warn "Python 3.9 or newer is needed."
    yesno "Install python3 now?" y && pkg_install python3 python3-venv python3-pip -- python3 python3-pip \
      || die "python3 is required"
  fi
  if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    warn "python3-venv is missing."
    local v; v="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    yesno "Install python3-venv now?" y && pkg_install "python3-venv" "python${v}-venv" -- python3 \
      || die "python3-venv is required"
  fi
  if [[ "$want_gui" == 1 ]] && command -v ldconfig >/dev/null && ! ldconfig -p | grep -q libxkbcommon-x11; then
    warn "The desktop window needs a few system libraries (Qt)."
    yesno "Install them now (libxkbcommon-x11-0 libxcb-cursor0 libegl1 libgl1)?" y && \
      pkg_install libxkbcommon-x11-0 libxcb-cursor0 libegl1 libgl1 -- libxkbcommon-x11 xcb-util-cursor mesa-libEGL mesa-libGL \
      || warn "skipped: if the window does not open, install them later"
  fi
}

# ── source folder ────────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC=""
find_source() {
  if [[ -f "$HERE/deploy/install.sh" ]]; then SRC="$HERE"; return; fi
  warn "This script is not inside the Rax Logic folder."
  if yesno "Download the code with git now?" y; then
    command -v git >/dev/null || { yesno "git is missing. Install it?" y && pkg_install git -- git || die "git is required"; }
    ask "Repository address" "$REPO_URL_DEFAULT"; local url="$REPLY"
    ask "Branch" "$BRANCH_DEFAULT"; local br="$REPLY"
    ask "Folder to put it in" "$HOME/rax"; local dir="$REPLY"
    if [[ -d "$dir/.git" ]]; then
      git -C "$dir" fetch origin "$br" && git -C "$dir" checkout "$br" && git -C "$dir" pull --ff-only origin "$br" \
        || die "could not update $dir"
    else
      git clone -b "$br" "$url" "$dir" || die "git clone failed (private repository? log in to GitHub or copy the folder by hand)"
    fi
    SRC="$(cd "$dir" && pwd)"
  else
    ask "Path of the Rax Logic folder (with deploy/install.sh)" "$HOME/rax"
    [[ -f "$REPLY/deploy/install.sh" ]] || die "no deploy/install.sh in $REPLY"
    SRC="$(cd "$REPLY" && pwd)"
  fi
}

INSTALL=""
run_install() {   # run_install [sudo] args...
  local pre=()
  if [[ "${1:-}" == sudo ]]; then shift; need_root; [[ -n "$SUDO" ]] && pre=(sudo); fi
  hr; say "Running: ${pre[*]} bash deploy/install.sh $*"; hr
  "${pre[@]}" bash "$INSTALL" "$@"
}

summary_ok() {
  hr; echo "${B}Summary${N}"; for l in "$@"; do [[ -n "$l" ]] && echo "   $l"; done; hr
  yesno "Go ahead?" y
}

# ── what is installed already ────────────────────────────────────────────────
server_installed() { [[ -x /opt/rax-logic/venv/bin/python ]]; }
client_installed() { [[ -x "${XDG_DATA_HOME:-$HOME/.local/share}/rax-logic/venv/bin/python" ]]; }

# ── flows ────────────────────────────────────────────────────────────────────
flow_server() {
  command -v systemctl >/dev/null || die "systemd (systemctl) not found: the server needs it"
  check_prereqs 0
  local port workers bind token_mode token="" fw=0 wh=""
  if [[ -f /etc/rax-logic/server.env ]]; then
    port="$(sed -n 's/^RAX_PORT=//p' /etc/rax-logic/server.env | tail -1)"
    say "A server is already set up (port ${port:-?}); its settings and token are kept."
  fi
  local suggest; suggest="$(free_port 8765)"
  while true; do
    ask "Port for the web page / API" "${port:-$suggest}"; port="$REPLY"
    [[ "$port" =~ ^[0-9]+$ ]] || { warn "a number please"; continue; }
    if port_busy "$port" && ! systemctl is-active --quiet rax-logic 2>/dev/null; then
      warn "port $port is used by another program (another dashboard?). Free one: $(free_port 8765)"; port=""; continue
    fi
    break
  done
  ask "Jobs that may run at the same time (criss-cross uses all CPU cores)" "2"; workers="$REPLY"
  choose "Who may connect?" "a=any PC on the network (0.0.0.0)" "l=only this computer (127.0.0.1)"
  [[ "$REPLY" == l ]] && bind=127.0.0.1 || bind=0.0.0.0
  if [[ ! -f /etc/rax-logic/server.env ]]; then
    choose "Login token for clients" "g=generate one (recommended)" "o=type my own" "n=no login (closed network only)"
    token_mode="$REPLY"
    if [[ "$token_mode" == o ]]; then ask "Token" ""; token="$REPLY"; [[ -n "$token" ]] || token_mode=g; fi
  else token_mode=keep; fi
  if command -v ufw >/dev/null && ${SUDO:-} ufw status 2>/dev/null | grep -q "Status: active"; then
    yesno "Firewall (ufw) is on. Open port $port for clients?" y && fw=1
  fi
  if yesno "Is this PC offline (install from a folder of downloaded wheels)?" n; then
    ask "Wheel folder (made with: bash deploy/install.sh bundle DIR)" "$SRC/wheels"; wh="$REPLY"
  fi
  local args=(server --port "$port" --workers "$workers" --bind "$bind")
  case "$token_mode" in o) args+=(--token "$token") ;; n) args+=(--no-token) ;; esac
  [[ -n "$wh" ]] && args+=(--wheelhouse "$wh")
  summary_ok "Install the SERVER (systemd service rax-logic, runs as user 'rax')" \
             "from: $SRC" "port: $port   listen on: $bind   parallel jobs: $workers" \
             "login token: $([[ $token_mode == keep ]] && echo 'keep existing' || echo "$token_mode")" \
             "$([[ $fw == 1 ]] && echo "open firewall port $port")" \
             "$([[ -n "$wh" ]] && echo "offline wheels: $wh")" || exit 0
  run_install sudo "${args[@]}" || die "server installation failed (see above)"
  [[ $fw == 1 ]] && { need_root; $SUDO ufw allow "$port/tcp" && say "Firewall: port $port opened"; }
  local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  hr; say "Server ready: http://${ip:-<server-ip>}:$port/"
  echo "   Token: sudo grep RAX_TOKEN /etc/rax-logic/server.env"
  echo "   Status: systemctl status rax-logic     Logs: journalctl -u rax-logic -f"
}

flow_client() {
  local kind gui=1 url="" token="" wh=""
  choose "Which client?" "d=desktop app + command line (needs a screen)" "c=command line only (rax-client)"
  kind="$REPLY"; [[ "$kind" == c ]] && gui=0
  [[ $EUID -eq 0 ]] && warn "You are root: the client will be installed for root. Usually run this as your normal user."
  check_prereqs "$gui"
  if yesno "Connect it to a Rax Logic server?" "$([[ $gui == 1 ]] && echo n || echo y)"; then
    while true; do
      ask "Server address, e.g. http://10.0.0.5:8765" ""; url="$REPLY"
      [[ -n "$url" ]] && break; warn "please type the address"
    done
    ask "Login token (blank if the server has none)" ""; token="$REPLY"
  fi
  if yesno "Is this PC offline (install from a folder of downloaded wheels)?" n; then
    ask "Wheel folder" "$SRC/wheels"; wh="$REPLY"
  fi
  local args=(client)
  [[ $gui == 0 ]] && args+=(--cli-only)
  [[ -n "$url" ]] && args+=(--server "$url" --token "$token")
  [[ -n "$wh" ]] && args+=(--wheelhouse "$wh")
  summary_ok "Install the CLIENT for user $USER: $([[ $gui == 1 ]] && echo 'desktop app + rax-client' || echo 'rax-client only')" \
             "from: $SRC" "$([[ -n "$url" ]] && echo "server: $url" || echo 'no server (desktop app runs jobs on this PC)')" \
             "$([[ -n "$wh" ]] && echo "offline wheels: $wh")" || exit 0
  run_install "${args[@]}" || die "client installation failed (see above)"
  hr; say "Client ready."
  [[ $gui == 1 ]] && echo "   Start: 'Rax Logic' in the applications menu, or run: rax-logic"
  echo "   Command line: rax-client --help"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
    if yesno "Add ~/.local/bin to your PATH (in ~/.bashrc)?" y; then
      echo 'export PATH=$HOME/.local/bin:$PATH' >> "$HOME/.bashrc"; say "Done: open a new terminal to use it"
    fi ;; esac
}

flow_update() {
  local s=0 c=0
  server_installed && s=1; client_installed && c=1
  [[ $s == 0 && $c == 0 ]] && die "nothing installed yet: choose Install"
  local pull=1
  yesno "Get the latest code with git first?" y || pull=0
  local extra=(); [[ $pull == 0 ]] && extra+=(--no-pull)
  summary_ok "Update: $([[ $s == 1 ]] && echo 'server ')$([[ $c == 1 ]] && echo 'client')" \
             "from: $SRC" "settings, token, port and jobs are kept" || exit 0
  [[ $s == 1 ]] && { run_install sudo update "${extra[@]}" || die "server update failed"; extra=(--no-pull); }
  [[ $c == 1 ]] && { run_install update "${extra[@]}" || die "client update failed"; }
}

flow_remove() {
  local s=0 c=0
  server_installed && s=1; client_installed && c=1
  [[ $s == 0 && $c == 0 ]] && die "nothing installed here"
  if [[ $s == 1 ]] && yesno "Remove the SERVER?" n; then
    local purge=()
    yesno "Also delete all jobs, outputs and server settings (token)?" n && purge=(--purge)
    summary_ok "Remove the server ${purge[*]:+and delete jobs + settings}" || exit 0
    run_install sudo uninstall-server "${purge[@]}"
  fi
  if [[ $c == 1 ]] && yesno "Remove the CLIENT of user $USER?" n; then
    summary_ok "Remove the client (settings in ~/.rax-logic are kept)" || exit 0
    run_install uninstall-client
  fi
}

# ── main ─────────────────────────────────────────────────────────────────────
hr; echo "${B}Rax Logic – Physical Progress Module: setup${N}"; hr
find_source
INSTALL="$SRC/deploy/install.sh"
[[ -f "$INSTALL" ]] || die "missing $INSTALL"
ver="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml" 2>/dev/null)"
say "Source: $SRC (version ${ver:-?})"
server_installed && say "Server installed: $(/opt/rax-logic/venv/bin/rax-server --version 2>/dev/null || echo yes)"
client_installed && say "Client installed for $USER"

choose "What do you want to do?" "i=install" "u=update what is installed" "r=remove" "q=quit"
case "$REPLY" in
  i) choose "Install what?" "s=server (this Linux machine runs jobs for others, web page)" \
                            "c=client (desktop app / command line on this PC)" \
                            "b=both server and client on this machine"
     case "$REPLY" in
       s) flow_server ;;
       c) flow_client ;;
       b) flow_server; flow_client ;;
     esac ;;
  u) flow_update ;;
  r) flow_remove ;;
  q) exit 0 ;;
esac
say "Finished."
