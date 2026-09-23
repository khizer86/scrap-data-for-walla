# WellnessLiving extraction — plan

Goal: for each business we onboard, log into WellnessLiving with that business's
own credentials, export five datasets, and leave behind both the raw file
WellnessLiving gave us and a normalised CSV.

Datasets per business:

| # | Dataset              | WellnessLiving report (confirmed 2026-09-22) |
|---|----------------------|----------------------------------------------|
| 1 | Clients / contacts   | Client Details (`login-profile`)             |
| 2 | Sales / transactions | All Sales (Thoth engine, reached via nav)    |
| 3 | Attendance / visits  | Attendance with Purchase Option Details (`visit-class-buy-detail`) |
| 4 | Memberships / passes | Memberships (`purchase-membership-list`)     |
| 5 | Upcoming bookings    | Class Schedule (`classes-schedule`)          |

Decisions already made: one login per business; pull data by clicking WL's own
Export buttons (not by scraping tables or reverse-engineering the API); keep the
raw download plus a normalised CSV.

---

## Part A — Playwright research checklist

Work through these in order. Each has a "prove it" exercise you can run against
our own login flow, so the research lands in this codebase rather than staying
theory. Docs: https://playwright.dev/python/docs/intro

### A1. Browser, context, page, and saved sessions
- `sync_playwright()`, `chromium.launch()`, `browser.new_context()`, `context.new_page()`
- `storage_state` — cookies + localStorage dumped to JSON and reloaded
- Why a *context* matters: it is the isolated profile; downloads and cookies belong to it
- **Already used** in `platforms/wellnessliving/auth.py` — read that file first, it is
  the working reference.
- Prove it: delete `.sessions/wellnessliving.json`, run the auth smoke test twice,
  watch the second run skip the login form.

### A2. Locators and auto-waiting
- `page.locator()`, `get_by_role()`, `get_by_text()`, `get_by_label()`
- `.first`, `.nth()`, `.filter(has_text=...)`, chaining
- Auto-waiting: a locator action waits for visible + stable + enabled by itself.
  This is the single biggest reason never to write a sleep.
- `expect(locator).to_be_visible()` vs `locator.wait_for(state="visible")`
- Prove it: rewrite one selector in `constants.py` as a `get_by_role` call and
  confirm it still finds the field.

### A3. Downloads — the core of this project
- `browser.new_context(accept_downloads=True)` (already set in our auth)
- `with page.expect_download(timeout=...) as info:` then `download = info.value`
- `download.suggested_filename`, `download.save_as(path)`, `download.failure()`
- The trap: the download event fires on the *context*, and the click that starts
  it must happen **inside** the `expect_download` block, or the event is missed.
- Multiple or queued downloads: `context.on("download", handler)`
- Prove it: download anything from any site into `outputs/` with `save_as`.

### A4. Menus, overlays and dialogs
WL's report navigation is App Drawer → View All → Reports → *report*, which means
popovers that close on blur.
- `locator.click()`, `hover()`, `press()`
- `page.on("dialog", ...)` for native confirm/alert boxes
- `frame_locator()` if any report renders inside an iframe (check during discovery)
- Prove it: script the App Drawer → Reports navigation and screenshot the result.

### A5. Date ranges and filters
- Filling a date `input` with `fill()` vs driving a calendar widget by clicking
- `select_option()` for dropdowns
- WL: **Filter** button → change fields → **Apply**. The report reloads via XHR, so
  you must wait for the *new* table, not just for the click.
- `page.wait_for_response(lambda r: "report" in r.url)` is the reliable wait here.
- Prove it: set a report to a fixed date range and confirm the row count changes.

### A6. Waiting correctly
- `wait_for_load_state("domcontentloaded" | "load" | "networkidle")` — and why
  `networkidle` is discouraged on apps that poll
- `wait_for_selector`, `wait_for_function`, `wait_for_response`
- `expect()` polling assertions with their own timeout
- Timeouts: context default vs per-call override (we set both in `config.py`)

### A7. Debugging tools — learn these early, they save hours
- `playwright codegen https://us.wellnessliving.com` — records your clicks as code
  and is the fastest way to harvest real selectors during discovery
- `PWDEBUG=1` → Playwright Inspector, step through actions
- `page.pause()` — drop a breakpoint mid-script with a live browser
- Tracing: `context.tracing.start(screenshots=True, snapshots=True)` then
  `context.tracing.stop(path="trace.zip")` then `playwright show-trace trace.zip`.
  Time-travel debugging; worth capturing on every failed run.
- `page.screenshot()` / `page.content()` on failure

### A8. Robustness patterns
- Retry a flow, not just a click; idempotent steps so a retry is safe
- Catch `TimeoutError` from `playwright.sync_api`, not bare `Exception`
- Screenshot + HTML dump on every failure, keyed by business name
- Candidate-selector lists (the pattern already in `constants.py`): first visible wins

### A9. Things to deliberately *not* do
- Sleeps — use waits
- Deep CSS/XPath chains tied to generated class names
- Assuming a click succeeded — assert the resulting state
- Running many businesses in parallel against one host early on; get it right serially first

---

## Part B — Architecture

```
scrap-data/
├── config.py                 # shared env config (done)
├── businesses.json           # per-business credentials + settings (gitignored)
├── run.py                    # CLI: run one business, or all
└── platforms/wellnessliving/
    ├── auth.py               # login + session cache (done)
    ├── constants.py          # URLs + candidate selectors (login part done)
    ├── reports.py            # report registry: name → nav path, filters, export
    └── extractor.py          # generic "open report → set range → export → save"
```

### Per-business credentials

`businesses.json` (gitignored, with `businesses.example.json` committed):

```json
[
  {
    "slug": "downtown-yoga",
    "name": "Downtown Yoga",
    "email": "...",
    "password": "...",
    "enabled": true
  }
]
```

Adding a new business is one entry in that file, then
`python run.py --business downtown-yoga`. No code change. That is the whole point
of this shape.

`auth.py` already accepts `email` / `password` / `session_file` arguments, so it
takes per-business credentials as-is — we only need a distinct session file per
slug (`.sessions/wellnessliving-<slug>.json`).

### Output layout

```
outputs/<slug>/<YYYY-MM-DD>/
├── raw/clients.csv            # exactly what WL handed us, untouched
├── raw/sales.csv
├── clients.csv                # normalised: consistent headers, typed dates
├── sales.csv
└── run.json                   # manifest: per-report status, row counts, timings, errors
```

Raw is never edited. Normalisation always reads raw, so a parsing fix can be
re-run without touching WellnessLiving again.

### Run flow

1. Load businesses, filter to enabled (or the one named on the CLI)
2. For each business: authenticate, reusing the cached session when it is valid
3. For each report in the registry: navigate → apply date range → export →
   capture download → save raw → normalise → record in the manifest
4. On failure: screenshot + trace + HTML dump, record the error, continue to the
   next report rather than aborting the whole business
5. Write `run.json`, close the browser, move to the next business

---

## Part C — Discovery results (done 2026-09-22)

Discovery is complete and all five datasets extract end to end. What follows is
what the live back office actually does, so nobody has to rediscover it.

### The back office is on `www`, not the regional host

Login starts at `us.wellnessliving.com/login`, bounces through passport, and
lands on `https://www.wellnessliving.com/?.id-region=1&...`. The regional host
is only an entry point. Being on `www` is not proof of login, though — the
public marketing site shares that host — so `auth.py` also requires a
back-office `/rs/` link on the page.

### Reports are directly addressable

No app-drawer walking is needed:

```
/rs/report-list.html?id_report_list_source=3   # the index
/rs/report-view.html?sid_report=<id>           # a report, by id
```

47 reports were enumerated this way. The five we use:

| Dataset | Report | id |
|---|---|---|
| clients | Client Details | `login-profile` |
| memberships | Memberships | `purchase-membership-list` |
| attendance | Attendance with Purchase Option Details | `visit-class-buy-detail` |
| upcoming_bookings | Class Schedule | `classes-schedule` |
| sales | All Sales | *(no id — see below)* |

**Two report engines exist.** The classic one uses `sid_report` ids. A newer
"Thoth" engine serves some reports, including All Sales, at URLs carrying a
per-session token:

```
/Thoth/Report/SalesReport/Transaction/TransactionAllReportPage.html?s=<token>
```

That token cannot be hardcoded. Those reports are reached by reading the href
out of the nav at runtime (`Report.nav_label`). The nav link is present in the
DOM but collapsed, so it must be read rather than clicked.

### Controls are `div`s with `js-` classes, not buttons

Text selectors scoped to `button`/`a` find nothing. Target the `js-` classes,
which are behavioural hooks; `css-` classes are styling and less stable.

| Control | Selector |
|---|---|
| Export | `div.js-control-button-export` |
| Format menu | `td.js-grid-gear-item-title` — labelled `CSV` / `Excel` / `PDF` |
| Date pill | `div.js-navigate-calendar` |
| Date fields | `input.js-date-start`, `input.js-date-end` (`YYYY-MM-DD`) |
| Apply | `button.js-btn-apply` |

### The two traps that cost the most time

**Reports open on the current week.** Export without widening the range and you
get a header row and nothing else — which looks exactly like an empty report.
Every history report needs an explicit lifetime range.

**The date widget needs Enter.** Filling both fields and pressing Apply silently
reverts to the original range. The commit sequence is: click the pill, then for
each field `fill()` **then press Enter**, then Apply. Tested four ways: fill+Enter
works, JS-set-plus-events works, plain fill+Apply and character-typing both fail.
Because a rejected range is indistinguishable from an empty report,
`_verify_date_range()` reads the range back and fails loudly if it did not take.

**Check-Ins is single-day.** `visit-attend-list` has no end-date field, so
history would need one run per day. We use the range-capable
`visit-class-buy-detail` instead. The extractor still handles single-day reports
by exporting one day and warning.

### Two back offices — the one that cost the most time

**Some accounts are served a different UI in headed Chrome than in headless.**
Confirmed 2026-09-23 on a 9,000-client studio: the same account, same URL, same
session.

| | classic toolbar | date picker | schedule button |
|---|---|---|---|
| headless | present | present | present |
| headed | **absent** | **absent** | present |

In headed mode the classic report page renders WellnessLiving's newer back
office — no Export control, no date picker, just a schedule. The only surviving
match was `.css-navigate-calendar`, which in that UI is the *Schedule* nav
button, so every attempt to open the date picker navigated to the calendar and
the run failed with a misleading "date field not found".

Consequences, all of which the code now handles:

- **Run headless.** `HEADLESS=true` is the default in `.env` for this reason.
  `--headed` is for clearing a first-login verification code, nothing else, and
  on such an account it cannot complete a report.
- `_check_classic_ui()` detects the new UI and says so directly, instead of
  letting it surface as a missing selector three minutes later.
- A small studio (Hiptwist) serves the classic UI either way, which is why this
  stayed hidden until the second business. **One business is not a sample.**

Diagnosing it took a screenshot from the failure artefacts - the logs alone kept
pointing at timing. When a selector "should be there" and isn't, look at the
page before theorising about waits.

### Also learned on the second business

- Reports open on the *current week* and take ~15s to render a decade of
  clients; a CSV export of 9,444 clients downloads in ~13s and the whole report
  takes ~86s end to end.
- The 15-second "Generated Reports" threshold in WL's docs did **not** trigger
  even at 9,444 rows. The queued path remains unexercised.

### Still from WL's docs, not yet hit in practice

**Async exports.** Reports taking 15 seconds or more queue to the Generated
Reports page (kept 90 days) instead of downloading. The fallback is implemented
(`_download_from_generated_reports`) but has not fired yet — this studio is
small enough that every export came back directly. Expect it on a larger
business, and confirm the selectors there then.

**Column customisation leaks into exports.** WL exports only the columns the
account last chose under Action → Customize, saved per staff account, so two
businesses can silently produce different shapes. `_check_headers()` warns when
expected columns are missing; only the clients report has its expected headers
filled in so far.

### Per business, still worth checking

- The login has the **"Export and print reports"** staff permission — without it
  the Export control is not rendered at all
- Whether the account sees a location picker, and whether reports need a
  location filter set

### Two behaviours confirmed from WL's own docs, both of which the code must handle

**Async exports.** Any report taking **15 seconds or more** does not download.
It queues to Reports → **Generated** (the menu shows a count), and the file is
fetched from the Generated Reports page via **Action → Export to CSV**, where it
is kept for 90 days. Large businesses will hit this constantly, so the extractor
needs both paths: race the download event against the report going async, then
poll the Generated Reports page until that row reaches a finished **Status**.

**Column customisation leaks into exports.** WL exports only the columns
previously selected under **Action → Customize**, and that selection is saved per
staff account. Two businesses will silently produce different columns unless we
either (a) assert the expected header on every export, or (b) set the columns
ourselves at the start of each run. Plan for (a) now, (b) if it bites.

### Also verify, per business

- The login has the **"Export and print reports"** staff role permission — without
  it the Export button is simply not rendered
- Whether the account sees a location picker (multi-location businesses), and
  whether reports need a location filter set

---

## Part D — Build order

| Step | Deliverable | Status |
|------|-------------|--------|
| 1 | `businesses.json` loader + `run.py` CLI | **done** |
| 2 | Per-business session files wired through `auth.py` | **done** |
| 3 | `extractor.py`: generic export engine | **done** |
| 4 | Discovery session — real selectors, URLs, report ids | **done** (Part C) |
| 5 | One report end-to-end | **done** — 330 clients |
| 6 | The remaining four reports | **done** — all five export |
| 7 | Normalisers + header assertions per report | partial — generic tidy-up works; per-report rules and `expected_headers` still to fill |
| 8 | Failure artefacts: trace, screenshot, HTML dump | **done** |
| 9 | Multi-business loop, run manifest, resume-on-failure | loop + manifest done; resume not started |

First full run, one business, five reports, about 2.5 minutes:

```
ok  clients             330 rows   30s  (download)
ok  memberships          83 rows   30s  (download)
ok  attendance          713 rows   29s  (download)
ok  upcoming_bookings    34 rows   29s  (download)
ok  sales               198 rows   30s  (download)
```

### What is left

- **Per-report normalisers** — parse dates to ISO, normalise phone numbers, and
  fill `expected_headers` for the four reports that lack them, so a column
  change is caught rather than silently absorbed
- **A second business** — everything so far is confirmed against one studio.
  The next one tests the multi-tenant path, and is where the Generated Reports
  fallback and any location picker are likely to first appear
- **Resume-on-failure** — re-run only the reports that failed
- **Attendance date span** — currently a 10-year window; confirm that covers the
  studio's full history

---

## Part E — Risks

- **Verification codes on new devices.** `auth.py` already detects this and asks
  for a non-headless run. Each new business needs one supervised first login to
  seed its cached session.
- **Rate limiting / account lockout.** Run businesses serially with pauses between
  them. Never loop a failing login.
- **Authorisation.** We are automating a back office with credentials the business
  owner gave us. Keep per-business written authorisation on file.
- **Report renames.** WL renames reports between releases. The registry keeps names
  in one place, and a rename should fail loudly with the list of report names
  actually found on the page.
- **Session expiry mid-run.** Detect the login form reappearing, re-authenticate
  once, resume.
