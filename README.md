# Rax Logic – Physical Progress Module

Generates the locking square sheet, criss-cross dispensation and the 3.47 / 5.16
dispensation documents from a station TOC.

You can use it three ways:

| Use | Install | Runs the job on |
|---|---|---|
| Desktop app | `deploy/install.sh client` (Linux) or `pip install ".[gui]"` (Windows) | this PC, or a server if "Run on server" is filled in |
| Web browser | nothing – open `http://<server>:8765/` | the server |
| Command line | `deploy/install.sh client --cli-only` | the server |

## Easiest: interactive setup

```bash
bash setup.sh
```

The script asks what to do and confirms before it starts:
* **i**nstall, **u**pdate or **r**emove;
* **s**erver, **c**lient or **b**oth;
* for a client, **d**esktop app or **c**ommand line only;
* port (it checks the port is free, e.g. not your other dashboard on 8080),
  jobs at a time, login accounts, server address and your login ID,
  offline wheel folder;
* **a** = manage login accounts (on a server: list, add, reset a password,
  disable, enable, remove).

It installs missing prerequisites (python3, python3-venv, the Qt libraries
for the desktop app, git) after asking y/n. It shows a summary to confirm,
then runs `deploy/install.sh`. Press Enter to take the default in
[brackets].

Copied on its own to a new machine, `setup.sh` offers to download the code
with git. Run it as your normal user: it uses sudo only for the server parts.

## .deb packages (Ubuntu / Debian)

Build installable packages once, then install them on any number of PCs with
no internet and no `~/rax` folder:

```bash
bash deploy/build-deb.sh            # or: client | server   [--out DIR] [--wheelhouse DIR]
# -> dist/rax-logic-client_3.2.1_amd64.deb   desktop app + rax-client
# -> dist/rax-logic-server_3.2.1_amd64.deb   server (systemd service rax-logic)

sudo apt install ./dist/rax-logic-client_3.2.1_amd64.deb   # menu "Rax Logic", or: rax-logic
sudo apt install ./dist/rax-logic-server_3.2.1_amd64.deb   # prints the first login ID + password
sudo rax-logic-user add --name "SSE/Sig ADI"               # server: one account per person
```

* Use `apt install ./file.deb`, not `dpkg -i`: apt also installs the screen
  libraries the desktop app needs.
* A package contains a ready Python environment, so it is tied to the Python
  version of the PC it was built on. Build on the same Ubuntu / Debian release
  as the target PCs, e.g. build on 24.04 for 24.04 PCs.
* To update, install the newer `.deb` the same way. To remove, run
  `sudo apt remove rax-logic-client` (or `rax-logic-server`). `apt purge`
  also deletes the server settings; jobs and accounts stay in
  `/var/lib/rax-logic`.
* The server package also puts the first login in
  `/root/rax-logic-first-login.txt`. Delete it once noted.
* `bash setup.sh` → **d** builds the packages too.

## Server (Linux, systemd)

```bash
git clone <repo> rax && cd rax
sudo bash deploy/install.sh server --port 8765 --workers 2
```

This creates the `rax` user and installs into `/opt/rax-logic/venv`. Settings go
in `/etc/rax-logic/server.env`, jobs in `/var/lib/rax-logic/jobs`, and the
service is `rax-logic` (`journalctl -u rax-logic -f`).

When it finishes, the installer creates the first login account (the
administrator) and prints its **login ID and password**. Use `--no-login` only
on a closed network.

### Login accounts

Every person gets their own login ID and password, made up by the server:

```bash
sudo bash deploy/install.sh user add --name "SSE/Sig ADI"      # prints Login ID + Password
sudo bash deploy/install.sh user list
sudo bash deploy/install.sh user reset rx04361                 # new password, logs them out
sudo bash deploy/install.sh user disable rx04361               # or: enable / remove
```

* The password is shown **once**. Only a hash of it is kept
  (`/var/lib/rax-logic/users.json`), so a lost password is reset, not looked
  up.
* `--admin` makes an administrator, who sees everybody's jobs. Other users see
  only their own jobs.
* A PC stays logged in for `RAX_SESSION_DAYS` (default 30). The PC keeps a
  session key, never the password. Reset, disable or remove ends a user's
  sessions at once, with no restart needed.
* Five wrong passwords in a row lock that login ID for a minute.
* Servers set up before 3.2.0 keep their old `RAX_TOKEN`. It still works, as a
  server key with access to all jobs, for scripts. People should use login
  IDs: make one per person, then remove `RAX_TOKEN` from
  `/etc/rax-logic/server.env` and restart.

* `RAX_WORKERS` is how many jobs run at once. Each criss-cross job uses every
  CPU core, so keep it small; extra jobs wait in a queue.
* Finished jobs are deleted after `RAX_KEEP_DAYS` days (default 30).
* The server speaks plain HTTP. To use it outside a trusted LAN, put it behind
  nginx or Apache with HTTPS.

## Client

```bash
bash deploy/install.sh client --server http://10.0.0.5:8765 --uid rx04361   # asks for the password
```

This installs the desktop app (applications menu → *Rax Logic*, or `rax-logic`)
and the `rax-client` command into `~/.local/share/rax-logic`. No root is needed.
Use `--cli-only` for just `rax-client`, which needs nothing beyond Python.

In the desktop app:
* Fill in **Run on server** to run jobs on the server. Outputs are downloaded
  into the output folder (field 02). The first time, the app asks for your
  login ID and password; **Login** next to the field logs in again.
* The web page has the login at the top right.
* Leave it blank to run on this PC. The run happens in a separate process,
  so the window stays responsive. **Stop** cancels a run.
* Press **Alt** for the menu bar. **Settings** has the text size, "always
  show the menu bar", "remember fields when closing" and the server login.
  Shortcuts: Ctrl+O open TOC, Ctrl+S / Ctrl+L save / load settings,
  Ctrl+G Gen TOC, F5 Run Fn, F1 help.

```bash
rax-client login --server http://10.0.0.5:8765 --uid rx04361      # asks for the password
rax-client whoami | logout
rax-client run --settings ~/.rax-logic/ADI_20261011_101500.txt   # file saved by "Save Settings"
rax-client run --toc ADI_TOC.xlsx --stn ADI --hs "S5,CO5=VIRAR" --hs "S6,S8=SBI" \
               --lines 18 --criss-cross --chainage ADI_CH.xlsx --bands "0-120,120-300,300+"
# isolation options 13-15: --iso YES|NO  --iso-distance 120 ("" = no limit)  --iso-rear-end NO|YES
rax-client check-chainage --toc ADI_TOC.xlsx --chainage ADI_CH.xlsx
rax-client jobs | status ID | download ID --out DIR | cancel ID
```

If a client install stops with `Cannot update time stamp of directory
'rax_logic.egg-info'`, update the code (`git pull`) and run it again. Since
3.2.1 the installer builds from a clean copy, so files left in the folder by an
earlier `sudo` install no longer get in the way.

**Update** to the latest version. This pulls the code, reinstalls and restarts
the service; settings, login accounts, port and jobs are kept:

```bash
cd rax
sudo bash deploy/install.sh update     # server
bash deploy/install.sh update          # client, on each PC
```

Add `--no-pull` if you copied the new files across yourself, e.g. on a USB stick.

**Offline PCs:** on a PC with internet, run `bash deploy/install.sh bundle wheels/`.
Copy the repository and `wheels/` across, then add `--wheelhouse wheels/` to
the install command.

**Uninstall:**
* Server: `sudo bash deploy/install.sh uninstall-server [--purge]`
* Client: `bash deploy/install.sh uninstall-client`

## Isolation preferred over dispensation (option 13, default YES)

Only one train is assumed to overshoot at a time; double overshoot is ignored.
A movement with an overlap stays within it. One without an overlap (calling-on,
shunt) overshoots its next stop signal along the movements possible from that
signal's post (CO21 → S31: the S31/SH31 routes), and onwards from post to post.
Facing points send it the way they lie, trailing points let it through. A
meeting counts only if the criss-cross point is within option 14 (metres from
the next stop signal, measured with the chainage file).

**Shared berthing.** The overshoot also follows movements backwards. The next
stop signal (S30, UP) and the opposite signal at the other end of the same
berthing line (S31, DN) share the berthing 01AT, 01BT. The DN movement S21 →
S31 came in over the track the UP overshoot runs out on. So CO38 → S30 is also
taken past S30 over S21 → S31 backwards (124T, 123T, 118T, 113T), even though
the TOC has no S30 → S21 movement. It then continues backwards over S3 → S21
into S3's train, with the criss-cross point at PT113. A backward path is used
only where the layout links each track circuit to the next.

Where that overshoot reaches another movement's **route**, the tool picks a
facing point outside both routes and overlaps to lock the other way. It chooses
the point that locks the fewest other movements:

* if no other movement needs it the other way, it is added to **ISO**;
* otherwise it is locked conditionally, only while the other movement is set.

**ISO-PT-N / ISO-PT-R** hold all isolation points of a movement: the TOC's,
the added ones, and the conditional ones with their condition, e.g.
`111/112, #139/140# with S33_39`. The locking, criss-cross and dispensation
texts use only the unconditional ones. ISO-ADDED says which points the search
added and for which pair.

**Rear-end movements (option 15, default NO).** Sometimes the overshoot meets
the other movement's route and then runs on along it in the same direction.
For example, CO38 → S30 overshoots onto the line S12 → S2 has just left, at
PT103, 1,711 m beyond S30. There is no criss-cross point there; only a
rear-end collision is possible. With NO, such pairs are not isolated. They are
listed in the ISO file as `rear-end ignored` and keep their dispensation. With
YES, isolation is searched for them like any other pair.

Isolated pairs need no 3.47 / 5.16 dispensation. Details, including each
overshoot path, are in `STN_<stn>ISO-gen…xlsx`.

## Missing chainage

For criss-cross, every signal and point used by a route needs a chainage. The
app no longer leaves gaps silently:

* **Before the run**, the desktop app lists the signals and points missing
  from the chainage file (or all of them if no file is attached). You can type
  values there. The web page has the same check (field 16), and `rax-client`
  takes `--add-chainage S12=1234.5`.
* Values you type are used for that run only. The chainage actually used is
  written to `…CH-USED…csv`, and your chainage file is never changed.
* Anything still missing is listed in TOC-CHECK and in `…CH-MISSING…csv`,
  ready to fill in. Pairs that need a missing chainage stay **locked**: they
  are never dispensed on a guessed distance.

## Development

```bash
pip install -e ".[all]"
make test          # 49 tests
make server        # http://127.0.0.1:8765
make run           # desktop app
```
