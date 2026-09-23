"""
The WellnessLiving reports we export, and how to reach each one.

Everything WellnessLiving-specific about a dataset lives in one Report entry:
how to navigate to it, whether it takes a date range, and what its exported
header should look like. Adding a sixth dataset later is one more entry.

Report ids and labels below were read off a live back office on 2026-09-22.

Two navigation styles exist, because WellnessLiving is mid-migration between
two report engines:

  sid_report  - the classic engine, addressable as
                /rs/report-view.html?sid_report=<id>. Stable, so we deep-link.
  nav_label   - the newer "Thoth" engine, whose URLs carry a per-session token
                (/Thoth/Report/.../SomeReportPage.html?s=<token>). The token
                cannot be hardcoded, so we read the href out of the nav at
                runtime and follow it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


# How a report's date range is decided.
#   none   - the report has no date range
#   past   - a window ending today, `days` wide (history)
#   future - a window starting today, `days` wide (upcoming bookings)
#
# `offset_days` shifts the window off today: 1 with date_mode="future" starts
# it tomorrow, which is what you want for reports that should exclude today.
DateMode = str

# Reports open on the current week, so history needs an explicit wide window.
# Ten years comfortably covers any studio's lifetime.
LIFETIME_DAYS = 3650


@dataclass(frozen=True)
class Report:
    """One exportable dataset."""

    key: str
    label: str
    sid_report: str | None = None
    nav_label: str | None = None
    date_mode: DateMode = "past"
    days: int = 365
    offset_days: int = 0
    expected_headers: tuple[str, ...] = ()
    notes: str = ""

    def date_range(self, today: date | None = None) -> tuple[date, date] | None:
        """
        The (start, end) window for this report, or None if it takes no dates.

        `offset_days` moves the whole window: it shifts the start for a future
        window and the end for a past one, so the span stays `days` wide.
        """
        today = today or date.today()
        anchor = today + timedelta(days=self.offset_days)

        if self.date_mode == "past":
            return anchor - timedelta(days=self.days), anchor
        if self.date_mode == "future":
            return anchor, anchor + timedelta(days=self.days)
        return None

    def __str__(self) -> str:
        return f"{self.label} [{self.key}]"


REPORTS: dict[str, Report] = {
    "clients": Report(
        key="clients",
        label="Client Details",
        sid_report="login-profile",
        date_mode="past",
        days=LIFETIME_DAYS,
        expected_headers=(
            "First name",
            "Last name",
            "Email",
            "Phone",
            "Client ID",
            "Status",
        ),
        notes=(
            "Confirmed exporting: name, email, phone, DOB, gender, address, "
            "client ID, referrer, status. Needs a lifetime date range - on the "
            "default current-week window it exports headers and no rows."
        ),
    ),
    "sales": Report(
        key="sales",
        label="All Sales",
        nav_label="All Sales",
        date_mode="past",
        days=LIFETIME_DAYS,
        notes=(
            "Lives on the newer Thoth engine at "
            "/Thoth/Report/SalesReport/Transaction/TransactionAllReportPage.html, "
            "reached by following the nav link. Its export controls have not "
            "been confirmed yet and may differ from the classic reports."
        ),
    ),
    "attendance": Report(
        key="attendance",
        label="Attendance with Purchase Option Details",
        sid_report="visit-class-buy-detail",
        date_mode="past",
        days=LIFETIME_DAYS,
        notes=(
            "One row per visit, with the purchase option it was booked against. "
            "Chosen over 'Check-Ins' (visit-attend-list) because Check-Ins is a "
            "single-day report - no end date - so history would need one run "
            "per day."
        ),
    ),
    "memberships": Report(
        key="memberships",
        label="Memberships",
        sid_report="purchase-membership-list",
        date_mode="past",
        days=LIFETIME_DAYS,
        notes=(
            "Active and past memberships. Pair with 'Expiring Purchase Options' "
            "(visit-last-list) or 'Visits Remaining' if we need sessions left."
        ),
    ),
    # --- Upcoming-window reports -------------------------------------
    # The Class Schedule report carries no client information, so these two
    # cover who is actually booked in the fortnight after the migration date.
    # Both run forward from the run date, and both are deliberately separate
    # entries from their historical counterparts above: same WellnessLiving
    # report, different window, different output file.
    "upcoming_attendance": Report(
        key="upcoming_attendance",
        label="Attendance with Purchase Option Details (upcoming)",
        sid_report="visit-class-buy-detail",
        date_mode="future",
        days=14,
        notes=(
            "Same report as `attendance`, run forward instead of back, to get "
            "the client behind each upcoming booking. Kept separate so the "
            "historical export stays a clean record of what has happened."
        ),
    ),
    "upcoming_unpaid_visits": Report(
        key="upcoming_unpaid_visits",
        label="Unpaid Visits Details (upcoming)",
        sid_report="visit-unpaid-detail",
        date_mode="future",
        days=14,
        notes=(
            "Upcoming bookings with nothing paid against them - the ones that "
            "need chasing before a migration."
        ),
    ),
    "projected_revenue": Report(
        key="projected_revenue",
        label="Projected Revenue",
        sid_report="purchase-auto-list",
        date_mode="future",
        offset_days=1,
        days=30,
        notes="Revenue expected from auto-renewing purchase options, month ahead.",
    ),
    "visits_remaining": Report(
        key="visits_remaining",
        label="Visits Remaining",
        nav_label="Visits Remaining",
        date_mode="future",
        offset_days=1,
        days=360,
        notes=(
            "Companion to `expiring_options`: visits left on active purchase "
            "options, for when the expiry report does not show what we need. "
            "Same window as that report so the two line up. Lives on the newer "
            "engine at /Wl/Visit/Remain/Report/VisitRemainReport.html with a "
            "per-session token, so it is reached via the nav link."
        ),
    ),
    "expiring_options": Report(
        key="expiring_options",
        label="Expiring Purchase Options",
        sid_report="visit-last-list",
        date_mode="future",
        offset_days=1,
        days=360,
        notes=(
            "Purchase options expiring in the year ahead, starting tomorrow - "
            "today is excluded deliberately, since anything expiring today is "
            "already too late to act on."
        ),
    ),
    "upcoming_bookings": Report(
        key="upcoming_bookings",
        label="Class Schedule",
        sid_report="classes-schedule",
        date_mode="future",
        days=90,
        notes=(
            "Forward-looking window. 'Booking Requests' (appointment-request) "
            "covers appointment requests rather than booked classes."
        ),
    ),
}

# The order a run works through them: most certain first, so a broken run fails
# late rather than early.
DEFAULT_REPORT_KEYS: tuple[str, ...] = (
    "clients",
    "memberships",
    "expiring_options",
    "visits_remaining",
    "projected_revenue",
    "attendance",
    "upcoming_bookings",
    "upcoming_attendance",
    "upcoming_unpaid_visits",
    "sales",
)


def get_reports(keys: list[str] | None = None) -> list[Report]:
    """Resolve report keys to Report objects, defaulting to all of them."""
    if not keys:
        return [REPORTS[key] for key in DEFAULT_REPORT_KEYS]

    unknown = [key for key in keys if key not in REPORTS]
    if unknown:
        raise KeyError(
            f"Unknown report: {', '.join(unknown)}. "
            f"Known reports: {', '.join(REPORTS)}"
        )
    return [REPORTS[key] for key in keys]
