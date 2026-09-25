# WellnessLiving extractor — runbook

Logs into the WellnessLiving staff back office as each business, exports ten
reports to CSV, and saves both the raw download and a cleaned copy.

This is the operating guide. For *why* the code is shaped the way it is — the
back office's two UIs, the date-picker quirks, the report ids — see
[PLAN.md](PLAN.md).

---

## Running it

From the project root (`scrap-data`):

```powershell
.venv\Scripts\activate.ps1     # once per terminal
python run.py                  # every enabled business, all 10 reports
```

Without activating, prefix with `uv run`: `uv run python run.py`.

| Command | What it does |
|---|---|
| `python run.py --list` | Businesses, reports and date windows. Touches nothing. |
| `python run.py` | All enabled businesses, all reports |
| `python run.py -b dryp-yoga` | One business |
| `python run.py -r sales -r clients` | Specific reports (repeatable) |
| `python run.py --date 2026-09-23` | Treat that as today, for date windows and the output folder |
| `python run.py --headed` | Show the browser — first login only, see below |
| `python run.py --trace` | Record a Playwright trace into the run's `debug/` folder |
| `python run.py --no-backup` | Extract, but don't copy to Google Drive |
| `python run.py -b dryp-yoga --backup-only` | Only copy today's (or `--date`'s) run to Drive |

Exit codes: `0` everything succeeded, `1` at least one report failed, `2` a
configuration problem (unknown business or report, bad `businesses.json`).
A failed Drive backup also exits `1`.

A full run is roughly 3–5 minutes per business, longer for a large one — a
9,000-client studio takes about 15 minutes.

---

## Adding a business

One entry in `businesses.json` (gitignored; `businesses.example.json` shows the
shape). No code change.

```json
{
  "slug": "new-studio",
  "name": "New Studio",
  "email": "owner@newstudio.example",
  "password": "...",
  "enabled": true,
  "history_start": "2020-01-01",
  "drive_folder": "9-24-26 - New Studio (WL)",
  "notes": "Anything worth remembering about this account."
}
```

`slug` names the output folder and the cached session file, so keep it
lowercase with hyphens and never change it afterwards.

Then clear the device verification once:

```powershell
python run.py -b new-studio --headed     # type the emailed code in the browser
python run.py -b new-studio              # headless from here on
```

Before the first run, check the login has the **"Export and print reports"**
staff permission. Without it WellnessLiving does not render the Export control
at all and every report fails.

---

## The three things that cause most failures

### 1. Run headless

`.env` sets `HEADLESS=true`. Leave it.

Some accounts serve WellnessLiving's **newer back office to a headed browser
and the classic one to headless** — same account, same URL. The extractor
drives the classic UI, so a headed run on such an account cannot export
anything. It fails with a message saying exactly this.

`--headed` exists only to clear a first-login verification code. If reports
fail during that run, that is expected; rerun headless.

### 2. `history_start` must come from evidence

History reports default to ten years back. That is safe but slow, and on a big
studio a ten-year sales export never finishes at all.

Setting `history_start` narrows it — but set it too late and **data is silently
cut off**. This has happened: a business assumed to have opened in 2024 in fact
had records from 2021, and the clamp dropped 22,481 attendance rows. The only
symptom was an export starting exactly on 1 January.

So: never set it from memory. Widen the window, export, look at the earliest
row, then set `history_start` a year before it. The extractor warns when a
report's earliest row lands on the first day of the requested window — if you
see that warning, widen and re-run.

### 3. Reports open on the current week

Every history report needs its date range set explicitly, which the extractor
does. If an export ever comes back with headers and no rows, that is the first
thing to suspect.

---

## Reports

Windows are recomputed from today (or `--date`) on every run.

| Key | WellnessLiving report | Window |
|---|---|---|
| `clients` | Client Details | history |
| `sales` | All Sales | history |
| `attendance` | Attendance with Purchase Option Details | history |
| `memberships` | Memberships | history |
| `upcoming_attendance` | Attendance with Purchase Option Details | today → +14d |
| `upcoming_unpaid_visits` | Unpaid Visits Details | today → +14d |
| `upcoming_bookings` | Class Schedule | today → +90d |
| `projected_revenue` | Projected Revenue | tomorrow → +30d |
| `expiring_options` | Expiring Purchase Options | tomorrow → +360d |
| `visits_remaining` | Visits Remaining | tomorrow → +360d |

"history" means `history_start` if set, otherwise ten years back.

Changing a window, or adding a report, is one entry in
[reports.py](reports.py) — no other code changes.

**A migrated business returns 0 rows for every forward-looking report.** That is
correct, not a failure. On a live business those six are the ones to check
first, since they have never yet returned data.

**`visits_remaining` currently fails on accounts that render it in the new UI**
(export behind a ⋮ menu, a year selector instead of a date range). It works on
accounts serving the classic layout.

---

## Output

```
outputs/<slug>/<YYYY-MM-DD>/
├── raw/clients.csv        exactly what WellnessLiving produced, untouched
├── clients.csv            cleaned: trimmed headers, blank rows and columns dropped
├── run.json               per-report status, row counts, timings, errors
└── debug/                 screenshots, page HTML and traces from failures
```

`run.json` merges across runs, so re-running two failed reports does not erase
the record of the eight that already succeeded that day.

Row counts in `run.json` are the reliable signal. An empty export has no header
row at all, so an empty file and a broken one look alike on disk.

### Google Drive backup

After each business finishes, its run is copied to
`<DRIVE_ROOT>\<drive_folder>\<YYYY-MM-DD>\` — the raw export of each successful
report plus `run.json`, flat in that folder. Cleaned CSVs and `debug/` stay
local; the cleaned files can always be rebuilt from raw.

- `DRIVE_ROOT` in `.env` is a Google Drive for Desktop path, e.g.
  `G:\Shared drives\Walla Onboarding + DM\Migrations`. `drive_folder` in
  `businesses.json` is the business's folder under it, as created at planning.
- The business folder is **not** created by the script. If it is missing, or
  Drive for Desktop isn't running, the backup fails loudly and the local files
  are untouched — create the folder and run `--backup-only`.
- Leave either setting empty to skip backup for that business.

---

## When something fails

The run continues past a failed report and records it, so one failure does not
cost you the rest.

| Message | Meaning |
|---|---|
| "showing WellnessLiving's newer back office" | Running headed on an account that needs headless — drop `--headed` |
| "Could not open the date panel" | The page was still loading, or it renders the new UI |
| "did not download, and WellnessLiving has no queued report" | The export exceeded its timeout — narrow the date range, or raise `download_timeout_ms` on that report |
| "Date range did not apply" | The picker rejected the range; the guard caught it before exporting the wrong window |
| "earliest row is … the first day of the requested window" | Probable truncation — widen `history_start` |
| "does not recognise \<email\>" | Wrong address; passport bounced it to signup |

For anything visual, look in `debug/` first — the screenshot usually shows the
answer faster than the logs do. Add `--trace` and open the result with
`playwright show-trace` to step through the run.
