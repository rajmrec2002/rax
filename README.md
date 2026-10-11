# Rax Logic – Physical Progress Module

Generates the locking square sheet, criss-cross dispensation and the 3.47 / 5.16
dispensation documents from a station TOC.

You can use it three ways:

| Use | Install | Runs the job on |
|---|---|---|
| Desktop app | `deploy/install.sh client` (Linux) or `pip install ".[gui]"` (Windows) | this PC, or a server if "Run on server" is filled in |
| Web browser | nothing – open `http://<server>:8765/` | the server |
| Command line | `deploy/install.sh client --cli-only` | the server |

## Server (Linux, systemd)

```bash
git clone <repo> rax && cd rax
sudo ./deploy/install.sh server --port 8765 --workers 2
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
./deploy/install.sh client --server http://10.0.0.5:8765 --token <token>
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

**Offline PCs:** on a PC with internet, run `./deploy/install.sh bundle wheels/`.
Copy the repository and `wheels/` across, then add `--wheelhouse wheels/` to
the install command.

**Uninstall:**
* Server: `sudo ./deploy/install.sh uninstall-server [--purge]`
* Client: `./deploy/install.sh uninstall-client`

## Isolation preferred over dispensation (option 13, default YES)

For each pair of movements the locking allows at the same time, the tool
finds points outside both routes and overlaps that, set in the right
position, stop a train on one from running onto the other. Isolated pairs need
no 3.47 / 5.16 dispensation.

* If no other movement needs the point the other way, it is added to the
  movement's **ISO** (see column ISO-ADDED).
* If others do, it is locked only while both movements are set. This
  conditional isolation goes in column **COND-ISO**:
  `#121N# with S12_12 set`.

Details are in `STN_<stn>ISO-gen…xlsx`. The track layout is worked out from
the TOC (track circuits in running order). Anything doubtful is listed in its
LAYOUT CHECK sheet and left with its dispensation.

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
