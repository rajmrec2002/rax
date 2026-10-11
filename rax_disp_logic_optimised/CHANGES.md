# Changes

## 2.3.0 – update command, interactive setup

* **All isolation points in ISO-PT-N / ISO-PT-R.** These columns hold the
  TOC's ISO points, the ones added by the isolation search, and the
  conditional ones with their condition:
  `111/112, 121, #109/110# with S38_3+S46_3`. A conditional point is locked
  only while one of the listed movements is set. Every function that reads
  these columns uses only the unconditional points: the locking (pairwise
  square sheet), criss-cross, the isolation search and the dispensation
  texts. The COND-ISO column is replaced by this; ISO-ADDED still says
  which points were added and for which pair. Helpers: `iso_split`,
  `iso_uncond`, `iso_join` in processing/point_format.py.
* Track circuits named after a calling-on signal that never appears in the
  TO column (e.g. UJN CO5T, CO11T, proved for calling-on clearance) are left
  out of the track layout used for isolation. They still count in the
  locking.

* Dispensation texts write isolation points like route and overlap points:
  `with isolation point/s (211/212N, 287/288N) & (275/276R)`, then
  `& track/s (…)`. This includes the points added by the isolation search.
  Before it was `with following isolation point/s (275/276R, 287/288)`,
  copied from the ISO column, with the N dropped.

* `bash setup.sh`: one-go interactive install / update / remove of server
  and client. It asks y/n and s/c questions, installs missing
  prerequisites, checks the port is free, confirms a summary, then runs
  deploy/install.sh.
* Fixed: adding the update command had dropped the installer's
  port-checking functions, so a busy port (e.g. another dashboard on 8080)
  was no longer refused. Restored.

* `sudo bash deploy/install.sh update` (server) / `bash deploy/install.sh
  update` (client) pulls the latest code, reinstalls and restarts the
  service. Settings, token, port and jobs are kept. Use `--no-pull` for files
  copied by hand. A client keeps its kind: command-line only, or desktop app.
* The installer always replaces the Rax Logic code itself, so an update
  takes effect even when the version number has not changed.
* Includes the berthing, post-to-post, isolation and overshoot changes
  below.


## 2.2.0 – isolation preferred over dispensation

* **New option 13 "Isolation (ISO) preferred over dispensation?"** (default
  YES) in the desktop app, the web page and `rax-client --iso YES|NO`.
* **Single overshoot only** (`processing/isolation.py`): one train is
  assumed to overshoot at a time; double overshoot is ignored, as for
  criss-cross movements.
  * A movement with an overlap stays within it (the overlap is its
    overshoot allowance, and its points are locked).
  * A movement without an overlap (calling-on, shunt) overshoots its next
    stop signal along the movements that can start from that signal's post
    (CO21 → S31: all routes of S31 / SH31 / CO31 / A31), then from the post
    at the end of each of those, and so on to the last stop signal. Those
    routes are not set, so their points are free.
  * The only limit is distance. A meeting counts if the criss-cross point
    (the point where the overshoot path enters the other route) is within
    option **14 "Isolation: min. criss-cross distance from next stop
    signal (m)"** of the next stop signal (default 120; blank = no limit).
    The distance is measured with the chainage file (field 03). Without
    chainage, only the movements from the next post are followed and every
    meeting on them counts.
  * An isolation point must be facing on the overshoot path, outside both
    movements' routes and overlaps. Its other leg must not lead onto the
    same route within 6 track circuits.
  * Isolation is needed where that overshoot reaches the other
    movement's **route**. Reaching only the other's overlap would need both
    trains to overshoot, and is ignored.
  * GRF example: CO21_01D overshooting S31 along S31_WCSL1 / S31_39 runs
    01BT-139T-(139/140 R)-140T-141T-146T into S33→S45's route at point 146,
    401.89 m from S31. Within the set distance, 139/140N goes into the ISO
    of S33_39. S17_04M / CO21_01D needs no isolation (double overshoot).
* **BERTH column** in the output TOC: each movement's berthing. A point
  track circuit is never berthing; CO-named track circuits (CO46T, proved
  for calling-on clearance) are not berthing; a list that ends on points
  (out of order, e.g. GRF S22_JKCL1D) takes the berthing of the other
  movements into the same post on the same line. Shunts into sidings have
  none, as no conflicting movement can follow.
* **Post-to-post rule in the locking too.** S, SH, A and CO from one post to
  the next post on the same line use the same track circuits and points.
  The TOC gives no track circuits for CO and no berthing for SH, by
  convention, so:
  * CO takes the track circuits of the S movement between the same posts on
    the same line. This was matched by signal number before (CO21 → S21); it
    is now matched by post;
  * an SH / A movement whose list does not end on its berthing gets it. The
    berthing is the last track circuits of RT-TC that carry no point number,
    usually a pair such as 01AT, 01BT or 05T, 05AT. It comes from:
    1. the S movement between the same posts; else
    2. a movement ending at the same next post on the same line; else
    3. any movement on the same line; else
    4. the line's track circuits by name (01T / 01AT / 01BT, never one with
       a point number).

  Effect: GRF unchanged (6,009 conflicting pairs); ADI 45,772 → 45,841
  (+69). The ADI additions are shunt movements now locked through their
  berthing, e.g. SH104_18D with 18T. Nothing was unlocked.

  An earlier draft of this rule matched berthing by name alone and took
  GRF 3AT/3BT (the approach of signal S3) as line 3's berthing; that is
  fixed.
* **Basic principles applied** (isolation):
  * a point track circuit carries the point number, at least 3 digits
    (121T; crossover 101/102 → 101T and 102T, or 101/102T); line track
    circuits (01AT, 3BT) have none;
  * facing / trailing is decided by point number and direction: DN (odd
    signal) → odd point ends facing; UP (even) → even ends facing. Diversion,
    and so isolation, is only possible at a facing point. This agrees with the
    TOC track-circuit order at all 2,994 checks for ADI and 548 of 564 for
    GRF; the 16 GRF exceptions are the S17/S19 lists at 112T already flagged
    as out of order;
  * S / SH / A / CO from one post to the next on the same line follow the
    same path and points to the same berthing. The shunt routes that leave
    out the berthing track circuits (GRF 32, ADI 85), or list them out of
    order (GRF SH17), take the matching S / CO list for isolation (locking
    unchanged).
* A pair already protected by an ISO point added for another pair is
  reported as isolated ("already in ISO of …") and its dispensation is
  dropped. It was shown as separated before.
* Layout check: links outvoted by the other routes (e.g. S38_01D listing
  01AT before 01BT in the UP direction) are set aside and listed. Only real
  ties hold pairs back as "check layout".
* The isolation point is a facing point outside both routes and overlaps,
  locked the other way. Where several cut every path, the one that locks
  the fewest other movements is chosen, Normal first:
  * nobody needs the other position: it is **added to ISO** (ISO-ADDED says
    for which pair);
  * otherwise it is **conditional**: COND-ISO `#139/140N# with S33_39 set`.

  Isolated pairs are left out of the 3.47 / 5.16 dispensation. Pairs that
  cannot be isolated (no free facing point on the overshoot path) keep it.
  Workbook `STN_<stn>ISO-gen…xlsx`: SUMMARY, PAIRS (with the overshoot
  path), BY MOVEMENT, LAYOUT CHECK, and the square sheet with isolated
  pairs marked I.
* The track layout is read from the TOC: RT-TC then OV-TC in running order,
  and the edge at a point used both Normal and Reverse is the toe.
  Doubtful orders are listed in LAYOUT CHECK, and pairs relying on them
  keep their dispensation (ADI: `581T/ALT`; GRF: e.g. the SH17 routes list
  109T last, S18/S20 swap 109T and 112T, S38 lists 01AT before 01BT).
* The ISO column in output TOCs keeps the R of a Reverse isolation point
  (`147/148, 438R`); a point without a letter is Normal. Before, N and R
  were both dropped, so a Reverse ISO point read as Normal there and in the
  3.47 / 5.16 texts.
* Corrected VV square sheet (option 08) could not read a square sheet saved
  by the tool: the route-name column was taken as data, so every cell was
  compared with the wrong partner. Fixed; a saved sheet fed back in now
  gives the same result.

## 2.1.0 – Linux server, client install, chainage prompt

* **Server** (`rax-server`, `server/`): web page plus REST API. Jobs run one
  per process, queued up to `RAX_WORKERS`; uploads and outputs are kept per
  job. Optional login token. Installed as a systemd service by
  `deploy/install.sh server`.
* **Client:** `deploy/install.sh client` installs the desktop app and
  `rax-client` (standard library only). The desktop app has a "Run on
  server" field; blank means run on this PC.
* **Desktop app runs jobs in a separate process** (`service/jobs.py`) and
  polls its progress. Before, the run was a thread inside the GUI that read
  widgets from that thread and could be started twice. A 5-minute GRF
  criss-cross run now never blocks the window for more than 0.1 s. Added a
  Stop button; Run and Gen TOC are disabled while a run is going.
* **Missing chainage is asked for and reported.** Before, the app never asked:
  a missing entry, or no chainage file at all, kept those pairs locked, and
  only the log said so.
  * The desktop app now lists the missing signals and points before the run
    and lets you enter them; the web page and `rax-client` have the same
    option.
  * Entered values are written to CH-USED; anything still missing goes to
    TOC-CHECK and a CH-MISSING template.
* **Criss-cross text is now the same on every run.** When an overlap point
  could be "busted", the point named in the text was taken from an unordered
  set, so it changed between runs (e.g. 133/134 vs 145/146 on GRF). The
  distances and locking were never affected. The lists are now sorted.
* Excel files now open without `python-calamine` (falls back to openpyxl);
  before, a plain install could not read `.xlsx` TOCs.
* `pyproject.toml` moved to the repository root and fixed (the old build
  backend name was invalid). Extras: `gui`, `server`, `fast`, `pdf`, `dev`.


## 2.0.1 – locking and dispensation fixes

Tested on GRF (`GRF_TOC_Final_01.04.2026.xlsx` + `GRF_CH-Signal_Point.xlsx`,
criss-cross 0–3000 m) and ADI. Regression tests: `make test`.

Check: the locking rules (point needed N by one route and R by the other,
shared track circuit, same line) were re-derived independently from the raw
TOC and compared pair by pair with the square sheet.

| Station | Conflicting pairs (rules) | Missed before | Missed now | Printed as permitted before | now |
|---|---|---|---|---|---|
| GRF | 6 009 | 214 | 0 | 98 | 0 |
| ADI | 45 772 | 1 620 | 0 | 2 351 | 0 |

### Conflict detection (`processing/locking.py`, `point_format.py`, `constants.py`)

| Bug | Effect | Fix |
|---|---|---|
| Route number stripped of `D`/`M` before the same-line lookup (`01D` → `01`) but the index kept `01D` | Two routes onto the same line (e.g. `CO21_01D` / `CO38_01D`) were not locked against each other | Both sides use `un_key()`: drops a trailing `D`/`M` after a digit and leading zeros (`01D` = `01M` = `1`, `5M` = `05M`) |
| Isolation points re-parsed with commas removed, so `824N, 821N` became one point `824821` | A route's own isolation points never matched; conflicts were found from one side only (`~`) | Isolation points read from `ISO-PT-N` / `ISO-PT-R` |
| `ISO-PT-N` kept only the last half of a crossover (`111/112N` → `112`) | Did not match `111/112R` in other routes | Full name kept |
| ISO track circuits blanked before the search | Isolation track circuits never caused a conflict | Kept from `toc_format()` |
| Track-circuit pattern `^\d+[A-Z]*T$` | `240/250T`, `259/260T`, `DMAT`, `JKCL1AT` … ignored | Pattern accepts `/` and letter names (`AXT` excluded) |
| `D`/`M` stripped from the ends of track-circuit lists | First/last name corrupted (`DMAT` → `AT`) | Only whitespace/punctuation stripped |
| Points limited to 3 digits | 2- or 4-digit points ignored | 1–4 digits |
| Run-together isolation entries `142T135T` | Read as one name | Split like `121N111/112N` already was |
| Calling-on routes have a blank (or `_`) RT-TC | CO routes never locked through track circuits | A CO<n> route with no RT-TC uses the RT-TC of the S<n> route to the same signal and line (TCK column shows them; RT-TC is left as entered) |
| Calling-on route lists overlap points but OV-TC is blank (GRF `CO3_JKCL1D`) | CO overlap never locked through track circuits | Such a CO route also takes the OV-TC of its S route (CO routes without overlap points do not) |
| No signal-post rule: routes from S5, CO5, SH5 and A5 (same post) were locked only if they happened to share a track circuit, point or line (earlier ADI sheet had 35 such pairs not fully locked, e.g. `S5_10M1` / `CO5_10D`) | Same-post routes could be dispensed together if the TOC omitted the first track circuit | Routes whose starting signals share a post number (`post_key()`: S/CO/SH/A + number) always conflict |
| Track circuits named with an underscore (UJN `1_2T`, `3_4T`, `01_01AT`, `10_10AT`) were not recognised | Routes sharing only such a track circuit were not locked | Pattern accepts `_` like `/`; any other unreadable entry (e.g. GRF `12T12`, ADI `471`, UJN `NLT2`) is listed in TOC-CHECK |
| A formatted TOC saved by an earlier run carries its old NEW-LOCK / NEW-LCK2 / NEW-DSP2 / TCK / END / texts | Old results could leak into the new run | Generated columns are cleared when the TOC is read |

### Square sheet and dispensation (`processing/square_sheet.py`, `criss_cross.py`)

* A conflict found from one side only is now **marked `~` on both cells and
  treated as a conflict everywhere** (lock list, dispensation, criss-cross).
  Before, `~` was dropped and the pair was printed as a permitted simultaneous
  movement.
* **Behaviour change for a corrected VV square sheet (option 08):** an `X`
  left on one side only now keeps the pair locked. To permit a pair, clear
  both cells.
* Criss-cross: a signal or point missing from the chainage file used to count
  as chainage 0, giving a false distance that could pass the min/max check.
  Missing chainage now means "no distance proof", so the pair stays
  conflicting, and the run logs which entries are missing.
* Criss-cross no longer rewrites the chainage file (it replaced blank or text
  chainages with 0 and dropped duplicates).

### Dispensation text (`processing/dispensation.py`)

* "up to S10 **with with** route point/s" and "up to S10 route point/s"
  (missing "with") fixed for main, sub and 5.16 texts.
* 5.16 texts now list isolation points (only shown before if the ISO cell
  literally said `YES`).
* Shunt lines no longer end with empty `()`; no double space after the line
  number.
* Permitted main movements are matched by exact route name, not by a regex
  substring.
* Criss-cross distance notes are separated from the movement text by a
  space (was `…121)[Dist.` / `vice versa.if OV point`), and the notes no
  longer contain double spaces (`to  FM`) or `S30(CH-…)`.
* Shunt lines print only the point groups a route has (no empty `& ()`).
* Direction rule (odd signal number = DN, even = UP) now uses the signal
  number, not the last character, so names like `S10A` are classified correctly.

### New

* **Criss-cross distance bands.** Field 12 "Distance bands (m)", e.g.
  `0-120, 120-300, 300+`. One set of 3.47 / 5.16 documents (and SQSH_CC,
  SQSH_XX, TOC, lists) is written per band, file names ending
  `_CC0-120m`, `_CC120-300m`, `_CC300m+`. A band `a-b` dispenses pairs whose
  criss-cross distance is > a and <= b; `300+` has no upper limit. Blank =
  old behaviour (min/max fields). Saved/loaded with the station settings.
* **TOC data check** (`STN_<stn>TOC-CHECK-gen-…xlsx`, every run): route points
  with no track circuit in RT-TC, overlap points with no OV-TC, CO routes with
  nothing to inherit, and permitted pairs where one route runs head-on to the
  other's start signal, and routes with the same signal, destination and
  overlap label but different overlap points (their printed headings read the
  same, e.g. ADI S5_06D / S5_06M both `OV1-113`). These are the TOC gaps that can make a conflicting
  movement look permitted.
* `processing/pipeline.py` (`run_dispensation`) holds the whole "Run Fn"
  sequence; the GUI calls it, so batch runs and the GUI give identical output.

* **App icon:** Indian Railways logo as window/taskbar icon and in the banner
  (`gui/assets/app_icon.png`, `.ico` for packaging).

### Not changed

* Home-signal auto-detect can pick wrong groups (on ADI it picked S87–S121).
  Check the groups before running.
