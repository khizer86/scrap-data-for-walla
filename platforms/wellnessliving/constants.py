"""
WellnessLiving constants: URLs, selectors and report identifiers.

Selectors were read off the live login page (2026-09-21). Each one is a list
of candidates - the first that is actually visible wins - so a markup change
degrades into a fallback instead of a hard failure.
"""

# --- URLs --------------------------------------------------------------

BASE_URL = "https://us.wellnessliving.com"

# Staff / business login. This redirects to the shared "passport" login on
# www.wellnessliving.com with region parameters attached.
LOGIN_URL = f"{BASE_URL}/login"

# Confirmed live 2026-09-22: the staff back office is served from the *www*
# host, not the regional one. After login we land on
#   https://www.wellnessliving.com/?.id-region=1&.region-src=...
# which is the dashboard. The regional host is only an entry point.
BACKOFFICE_URL = "https://www.wellnessliving.com"

AUTHENTICATED_URL_PREFIX = BACKOFFICE_URL

# Being on the www host is not enough on its own - the public marketing site
# lives there too. The back office is identified by its internal /rs/ links
# (report-list, schedule-list, ...), which the marketing pages never carry.
BACKOFFICE_MARKER_SELECTORS = (
    "a[href*='/rs/']",
    "a[href*='/Wl/Report/']",
)

# URL fragments that mean we are still somewhere in the login flow.
LOGIN_URL_MARKERS = (
    "/login",
    "/signin",
    "/sign-in",
    "/passport/",
)


# --- Back office deep links --------------------------------------------
# Confirmed live 2026-09-22 from the dashboard's own links. Reports are
# addressable by a report id, so we can navigate straight to one instead of
# walking the app drawer.

REPORT_LIST_URL = f"{BACKOFFICE_URL}/rs/report-list.html?id_report_list_source=3"
REPORT_VIEW_URL = f"{BACKOFFICE_URL}/rs/report-view.html?sid_report={{sid_report}}"
SCHEDULE_LIST_URL = f"{BACKOFFICE_URL}/rs/schedule-list.html?dt_date={{date}}"

# Passport bounces unrecognised addresses into the signup flow, which is a
# much clearer failure to report than a generic "login failed".
UNKNOWN_ACCOUNT_URL_MARKERS = ("register-begin",)


# --- Login form selectors ----------------------------------------------
# Confirmed live: the form posts login/pwd, not an email-typed input.

EMAIL_SELECTORS = (
    "#template-passport-login",
    "input[name='login']",
    "input[type='email']",
    "input[name='email']",
)

PASSWORD_SELECTORS = (
    "#template-passport-password",
    "input[name='pwd']",
    "input[type='password']",
)

SUBMIT_SELECTORS = (
    "button.js-button-next",
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Sign In')",
)

# "Keep me signed in" - checked so the cached session lasts longer.
REMEMBER_ME_SELECTORS = ("#passport-login-remember-password", "input[name='remember']")

# Shown when login failed (wrong password, locked account, ...).
ERROR_SELECTORS = (
    ".css-form-error",
    ".error",
    ".alert-danger",
    "[role='alert']",
)

# Present but hidden on a clean load; appears after repeated failed attempts.
CAPTCHA_SELECTORS = ("input[name='s_captcha']",)

# Shown when WellnessLiving emails a verification code for a new device. We
# cannot solve this automatically - the run pauses so you type it by hand.
VERIFICATION_SELECTORS = (
    "input[autocomplete='one-time-code']",
    "input[name*='code' i]",
    "input[id*='verification' i]",
)


# --- Back office navigation --------------------------------------------
# The app drawer is not needed: reports are directly addressable (above), which
# is both faster and far less brittle than walking a popover menu.
#
# Reports on the newer "Thoth" engine carry a per-session token in their URL,
# so they cannot be deep-linked. For those we load a classic report first and
# read the link out of its nav, which lists every report.
NAV_HOST_REPORT = "login-profile"

# Report pages load their grid over XHR well after domcontentloaded, and the
# toolbar is not usable before that. Measured at ~6s on a small studio; 9s
# leaves room for a bigger one.
SETTLE_MS = 9000


# --- Report page controls ----------------------------------------------
# Confirmed live 2026-09-22 on the Client Details report. WellnessLiving builds
# its controls from <div>s with js- prefixed classes, not <button>/<a>, so text
# selectors scoped to buttons find nothing. The js- classes are behavioural
# hooks and are the stable thing to target; css- classes are styling.

# Export sits in the report header: <div class="js-control-button-export">.
EXPORT_BUTTON_SELECTORS = (
    "div.js-control-button-export",
    "[s_id='export']",
    "[id*='control-button-export']",
    ":text-is('Export')",
)

# Kept as a fallback: some report layouts group Export under an Action menu.
ACTION_BUTTON_SELECTORS = (
    "div.js-control-button-action",
    "button:has-text('Action')",
    "a:has-text('Action')",
)

# The Export menu is a grid of <td class="js-grid-gear-item-title"> cells
# labelled exactly "CSV", "Excel" and "PDF". We take CSV: WellnessLiving's
# "Excel" option returns a CSV anyway, and PDF is useless to us.
EXPORT_CSV_SELECTORS = (
    "td.js-grid-gear-item-title:text-is('CSV')",
    ".js-grid-gear-item-title:text-is('CSV')",
    "td:text-is('CSV')",
)

FILTER_BUTTON_SELECTORS = (
    "div.js-control-button-advanced-filter",
    "div.js-advanced-filter",
    "div.js-control-panel-button:has-text('Filter')",
    ":text-is('Filter')",
)

APPLY_BUTTON_SELECTORS = (
    "button.js-btn-apply",
    "button.applyBtn",
    "button:has-text('Apply')",
)


# --- Date range --------------------------------------------------------
# Confirmed live 2026-09-22. Reports open on the current week and the range
# must be widened explicitly, or an export comes back with headers and no rows.
#
# The visible "Sep 20, 2026 - Sep 26, 2026" pill is a div, not an input. It
# opens a calendar panel holding the two real fields. Typing into the summary
# input directly does NOT work - the widget rewrites it and discards the value.
# The sequence that works: click the pill, fill both fields, click Apply.

DATE_INPUT_FORMAT = "%Y-%m-%d"

# The pill that opens the calendar panel.
DATE_RANGE_TOGGLE_SELECTORS = (
    "div.js-navigate-calendar",
    ".css-navigate-calendar",
)

DATE_FROM_SELECTORS = (
    "input.js-date-start",
    ".js-date-start",
)

DATE_TO_SELECTORS = (
    "input.js-date-end",
    ".js-date-end",
)

# The summary input inside the pill. Read it to confirm the applied range;
# never write to it.
DATE_SUMMARY_SELECTOR = "input.js-datepicker-input"

# Shown when the staff role lacks "Export and print reports", which renders the
# Export control missing rather than disabled - worth reporting precisely.
PERMISSION_DENIED_SELECTORS = (
    ":text('do not have permission')",
    ":text('Access denied')",
)


# --- Generated Reports -------------------------------------------------
# Any report taking 15 seconds or more to build does NOT download directly:
# WellnessLiving queues it to the Generated Reports page (kept for 90 days),
# where it is fetched with Action > Export to CSV. Every extraction therefore
# needs both paths. See PLAN.md, Part C.

GENERATED_REPORTS_URL = f"{BASE_URL}/report/generated"  # PROVISIONAL

GENERATED_REPORTS_LINK_SELECTORS = (
    "a:has-text('Generated')",
    "button:has-text('Generated')",
)

# A row in the Generated Reports table; matched by report name at runtime.
GENERATED_ROW_SELECTOR = "tr"

# Status cell text that means the file is ready to export.
GENERATED_READY_STATUSES = ("complete", "completed", "ready", "finished", "done")
GENERATED_FAILED_STATUSES = ("failed", "error", "cancelled", "canceled")

# How long we wait for a direct download before assuming the report went async
# and switching to the Generated Reports page. WellnessLiving's own threshold is
# 15 seconds; we allow a little slack for a slow network.
ASYNC_EXPORT_THRESHOLD_MS = 25_000


# --- Reports -----------------------------------------------------------
# The report registry lives in reports.py, which carries per-report navigation,
# date handling and expected headers. The API path (WlReportSid) is parked here
# for when the WellnessLiving app auth codes arrive.
