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
  jobs at a time, login token, server address, offline wheel folder.

It installs missing prerequisites (python3, python3-venv, the Qt libraries
for the desktop app, git) after asking y/n. It shows a summary to confirm,
then runs `deploy/install.sh`. Press Enter to take the default in
[brackets].

Copied on its own to a new machine, `setup.sh` offers to download the code
with git. Run it as your normal user: it uses sudo only for the server parts.

## Server (Linux, systemd)

```bash
git clone <repo> rax && cd rax
sudo bash deploy/install.sh server --port 8765 --workers 2
```

This creates the `rax` user and installs into `/opt/rax-logic/venv`. Settings go
in `/etc/rax-logic/server.env`, jobs in `/var/lib/rax-logic/jobs`, and the
service is `rax-logic` (`journalctl -u rax-logic -f`).

When it finishes, the installer prints the web address and a **login token**.
Clients need the token. Use `--no-token` only on a closed network.

* `RAX_WORKERS` is how many jobs run at once. Each criss-cross job uses every
  CPU core, so keep it small; extra jobs wait in a queue.
* Finished jobs are deleted after `RAX_KEEP_DAYS` days (default 30).
* The server speaks plain HTTP. To use it outside a trusted LAN, put it behind
  nginx or Apache with HTTPS.

## Client

```bash
bash deploy/install.sh client --server http://10.0.0.5:8765 --token <token>
```

This installs the desktop app (applications menu → *Rax Logic*, or `rax-logic`)
and the `rax-client` command into `~/.local/share/rax-logic`. No root is needed.
Use `--cli-only` for just `rax-client`, which needs nothing beyond Python.

In the desktop app:
* Fill in **Run on server** to run jobs on the server. Outputs are downloaded
  into the output folder (field 02).
* Leave it blank to run on this PC. The run happens in a separate process,
  so the window stays responsive. **Stop** cancels a run.

```bash
rax-client run --settings ~/.rax-logic/ADI_20261011_101500.txt   # file saved by "Save Settings"
rax-client run --toc ADI_TOC.xlsx --stn ADI --hs "S5,CO5=VIRAR" --hs "S6,S8=SBI" \
               --lines 18 --criss-cross --chainage ADI_CH.xlsx --bands "0-120,120-300,300+"
rax-client check-chainage --toc ADI_TOC.xlsx --chainage ADI_CH.xlsx
rax-client jobs | status ID | download ID --out DIR | cancel ID
```

**Update** to the latest version. This pulls the code, reinstalls and restarts
the service; settings, token, port and jobs are kept:

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

Where that overshoot reaches another movement's **route**, the tool picks a
facing point outside both routes and overlaps to lock the other way. It chooses
the point that locks the fewest other movements:

* if no other movement needs it the other way, it is added to **ISO**;
* otherwise it is locked conditionally, in column **COND-ISO**:
  `#139/140N# with S33_39 set`.

Isolated pairs need no 3.47 / 5.16 dispensation. Details, including each
overshoot path, are in `STN_<stn>ISO-gen…xlsx`.

## Missing chainage

For criss-cross, every signal and point used by a route needs a chainage. The
app no longer leaves gaps silently:

* **Before the run**, the desktop app lists the signals and points missing
  from the chainage file (or all of them if no file is attached). You can type
  values there. The web page has the same check (field 13), and `rax-client`
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
