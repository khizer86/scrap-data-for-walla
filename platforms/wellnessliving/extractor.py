"""
Export WellnessLiving reports to disk.

One engine drives every report: open it, set its date range, press Export, and
catch the file. The only per-report knowledge lives in reports.py.

Two WellnessLiving behaviours shape this module:

1. A report taking 15 seconds or more to build does not download. WellnessLiving
   queues it to the Generated Reports page instead, and the file is fetched from
   there. So every export races a download against that timeout and falls back.

2. Exports honour whatever columns the staff account last selected under
   Action > Customize. Headers are therefore verified, not trusted, once we know
   what each report should look like (Report.expected_headers).

Raw downloads are saved untouched. Normalisation always reads the raw file, so a
parsing fix can be re-run without going back to WellnessLiving.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger
from playwright.sync_api import Download, Page, TimeoutError as PlaywrightTimeout

import config
from businesses import Business
from platforms.wellnessliving import constants as c
from platforms.wellnessliving import ui
from platforms.wellnessliving.reports import Report


class ReportExportError(RuntimeError):
    """A report could not be exported."""


@dataclass
class ExportResult:
    """What happened to one report, for the run manifest."""

    report_key: str
    label: str
    status: str  # "ok" | "failed"
    raw_path: Path | None = None
    csv_path: Path | None = None
    rows: int | None = None
    elapsed_s: float = 0.0
    via: str = ""  # "download" | "generated-reports"
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "report": self.report_key,
            "label": self.label,
            "status": self.status,
            "raw_path": str(self.raw_path) if self.raw_path else None,
            "csv_path": str(self.csv_path) if self.csv_path else None,
            "rows": self.rows,
            "elapsed_s": round(self.elapsed_s, 1),
            "via": self.via,
            "error": self.error,
        }


class WellnessLivingExtractor:
    """Exports reports for one business, using an already-authenticated page."""

    def __init__(self, page: Page, business: Business, run_date: date | None = None):
        self.page = page
        self.business = business
        self.run_date = run_date or date.today()

        self.output_dir = business.output_dir(self.run_date.isoformat())
        self.raw_dir = self.output_dir / "raw"
        self.debug_dir = self.output_dir / "debug"

        # Report grids arrive over XHR after the page "loads", so every
        # navigation is followed by a settle wait before we touch the toolbar.
        self.settle_ms = c.SETTLE_MS

    # --- public API ----------------------------------------------------

    def extract_all(self, reports: list[Report]) -> list[ExportResult]:
        """
        Export every report, carrying on after failures.

        One broken report should not cost us the other four, so failures are
        recorded and the run continues.
        """
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for report in reports:
            results.append(self.extract(report))
        return results

    def extract(self, report: Report) -> ExportResult:
        """Export one report. Never raises - failures come back as a result."""
        logger.info(f"[{self.business.slug}] {report.label}")
        started = time.monotonic()
        result = ExportResult(report_key=report.key, label=report.label, status="failed")

        try:
            self._open_report(report)
            self._apply_date_range(report)

            download, via = self._export(report)
            result.via = via
            result.raw_path = self._save_raw(report, download)
            result.csv_path, result.rows = self._normalise(report, result.raw_path)
            result.status = "ok"
            logger.success(
                f"[{self.business.slug}] {report.label}: "
                f"{result.rows} rows via {via}"
            )

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.error(f"[{self.business.slug}] {report.label} failed - {result.error}")
            self._capture_failure(report)

        result.elapsed_s = time.monotonic() - started
        return result

    # --- navigation ----------------------------------------------------

    def _open_report(self, report: Report) -> None:
        """
        Get to the report page.

        Classic reports have stable, addressable URLs, so we go straight there.
        Thoth reports do not - their URL carries a per-session token - so we
        read the link out of the nav and follow it.
        """
        for attempt in range(1, c.OPEN_REPORT_ATTEMPTS + 1):
            if report.sid_report:
                url = c.REPORT_VIEW_URL.format(sid_report=report.sid_report)
                self.page.goto(url, wait_until="domcontentloaded")
            elif report.nav_label:
                self._follow_nav_link(report)
            else:
                raise ReportExportError(
                    f"{report.key} has neither sid_report nor nav_label - "
                    "reports.py does not say how to reach it."
                )

            # The back office is a single-page app: right after login it can
            # finish its own routing *after* our navigation and replace the
            # report with the schedule. Landing elsewhere is not an error we
            # can detect later - on the schedule page a `.css-navigate-calendar`
            # element still exists, so every subsequent step misfires quietly.
            self.page.wait_for_timeout(3000)
            if self._on_report_page(report):
                break

            logger.warning(
                f"Navigation landed on {self.page.url} instead of "
                f"{report.label} - retrying (attempt {attempt})."
            )
        else:
            raise ReportExportError(
                f"Could not stay on {report.label} after "
                f"{c.OPEN_REPORT_ATTEMPTS} attempts; the back office kept "
                f"redirecting to {self.page.url}."
            )

        self._wait_for_report_ready()
        self._check_classic_ui(report)
        self._check_permission()

    def _on_report_page(self, report: Report) -> bool:
        """
        Are we actually on the report we asked for?

        This is a URL check only. Whether the page is the *classic* UI we can
        actually drive is decided later by _check_classic_ui(), once
        _wait_for_report_ready() has given the toolbar time to render -
        checking it here would retry needlessly on a slow but healthy page.
        """
        url = self.page.url
        if report.sid_report:
            return f"sid_report={report.sid_report}" in url
        return "/Wl/" in url or "/Thoth/" in url

    def _check_classic_ui(self, report: Report) -> None:
        """
        Fail with the real reason when we are stuck on the newer back office.

        Discovered on a 9,000-client studio: the same account serves the classic
        reports UI to headless Chrome and the newer UI to a headed browser. The
        extractor drives the classic UI, so a headed run on such an account
        cannot work, and the symptom - a missing toolbar - looks nothing like
        the cause.

        Only classic (`sid_report`) reports are checked. Reports on the newer
        Thoth and /Wl/ engines legitimately have a different toolbar, and
        judging them by the classic one condemns pages that work fine.
        """
        if not report.sid_report:
            return

        if self.page.locator(c.EXPORT_BUTTON_SELECTORS[0]).count():
            return

        raise ReportExportError(
            "This account is showing WellnessLiving's newer back office, which "
            "has no classic report toolbar to drive. Some accounts serve the "
            "classic UI to headless Chrome and the new one to a headed browser "
            "- try running without --headed (HEADLESS=true)."
        )

    def _wait_for_report_ready(self) -> None:
        """
        Wait for the report's toolbar to actually exist.

        A fixed sleep is not good enough: a big studio's report takes far longer
        to build than a small one, and interacting early has real consequences -
        the date pill is a `js-navigate-calendar` element whose click falls
        through to "go to the calendar" until its handler is bound, which
        silently lands us on the schedule page instead of opening the picker.
        """
        for selector in (c.EXPORT_BUTTON_SELECTORS[0], c.DATE_SUMMARY_SELECTOR):
            try:
                self.page.wait_for_selector(
                    selector, state="visible", timeout=c.REPORT_READY_TIMEOUT_MS
                )
            except PlaywrightTimeout:
                logger.debug(f"Report toolbar element never appeared: {selector}")

        # The grid keeps loading after the toolbar renders, and the pill's
        # handler is bound in that window.
        self.page.wait_for_timeout(self.settle_ms)

    def _follow_nav_link(self, report: Report) -> None:
        """
        Navigate to a report by reading its href out of the nav.

        The link is present in the DOM but collapsed, so it cannot be clicked;
        we take its href and navigate directly instead. Any classic report page
        carries the full nav, so we start from one we know works.
        """
        if "report-view.html" not in self.page.url:
            self.page.goto(
                c.REPORT_VIEW_URL.format(sid_report=c.NAV_HOST_REPORT),
                wait_until="domcontentloaded",
            )
            self.page.wait_for_timeout(self.settle_ms)

        link = self.page.get_by_text(report.nav_label, exact=True).first
        if not link.count():
            raise ReportExportError(
                f"No nav entry named '{report.nav_label}'. The report may have "
                "been renamed - check reports.py against the back office."
            )

        href = link.get_attribute("href")
        if not href:
            raise ReportExportError(
                f"Nav entry '{report.nav_label}' has no href to follow."
            )

        logger.debug(f"Following nav link: {href}")
        self.page.goto(href, wait_until="domcontentloaded")

    def _check_permission(self) -> None:
        """Fail clearly when the account lacks report access."""
        message = ui.visible_text(
            self.page, c.PERMISSION_DENIED_SELECTORS, "permission warning"
        )
        if message:
            raise ReportExportError(
                f"{self.business.name} cannot open this report: {message}. "
                "The staff role needs the 'Export and print reports' permission."
            )

    # --- filters -------------------------------------------------------

    def _apply_date_range(self, report: Report) -> None:
        """
        Set the report's date window.

        Reports open on the current week, so this is not optional: skip it and
        the export returns headers with no rows. The date fields live inside a
        calendar panel that the date pill opens, and only commit on Apply.
        """
        window = report.date_range(self.run_date)
        if not window:
            return

        start, end = window

        # Never ask for data from before the business existed. The default
        # ten-year history is a safe guess, not a useful one: on a studio that
        # opened in 2024 it adds eight empty years, and a big sales export over
        # that range does not complete at all.
        opened = self.business.history_start_date
        if opened and start < opened:
            logger.debug(f"Clamping start {start} to {self.business.slug}'s {opened}")
            start = opened

        logger.debug(f"Date range: {start} to {end}")
        self._applied_window = (start, end)

        from_box = self._open_date_panel(report)

        # Some reports (Check-Ins, for one) cover a single day and have no end
        # field at all. Those export one day per run rather than a range.
        to_box = ui.first_visible(
            self.page, c.DATE_TO_SELECTORS, "date-to field", required=False
        )
        if to_box is None:
            logger.warning(
                f"{report.label} is a single-day report - exporting {end} only. "
                "Use a range-capable report if you need history."
            )
            start = end

        fields = [(from_box, start)] + ([(to_box, end)] if to_box else [])

        # Enter after each field is not optional. The widget only reads a typed
        # date when the field is committed; filling both and pressing Apply
        # leaves the original range in place, silently.
        for box, value in fields:
            box.fill(value.strftime(c.DATE_INPUT_FORMAT))
            box.press("Enter")
            self.page.wait_for_timeout(1200)

        ui.click_first_visible(self.page, c.APPLY_BUTTON_SELECTORS, "Apply button")
        self._wait_for_report_refresh()
        self._verify_date_range(start, end)

    def _open_date_panel(self, report: Report):
        """
        Open the calendar panel and return the start-date field.

        Clicking the pill before its handler is bound navigates to the schedule
        instead of opening the picker, so we check where we ended up and retry
        from the report page rather than failing on the first miss.
        """
        for attempt in range(1, c.DATE_PANEL_ATTEMPTS + 1):
            ui.click_first_visible(
                self.page, c.DATE_RANGE_TOGGLE_SELECTORS, "date range pill"
            )
            self.page.wait_for_timeout(2000)

            from_box = ui.first_visible(
                self.page, c.DATE_FROM_SELECTORS, "date-from field", required=False
            )
            if from_box is not None:
                return from_box

            if "report-view" not in self.page.url and "/Wl/" not in self.page.url:
                logger.warning(
                    f"Clicking the date pill navigated away to {self.page.url} - "
                    f"the report was not ready. Reopening (attempt {attempt})."
                )
                self._open_report(report)
            else:
                logger.debug(f"Date panel did not open (attempt {attempt}); waiting")
                self.page.wait_for_timeout(self.settle_ms)

        raise ReportExportError(
            f"Could not open the date panel for {report.label} after "
            f"{c.DATE_PANEL_ATTEMPTS} attempts. The report may be too slow to "
            "load - try raising REPORT_READY_TIMEOUT_MS or SETTLE_MS."
        )

    def _verify_date_range(self, start: date, end: date) -> None:
        """
        Confirm the range actually took.

        The widget silently discards values it does not like, and a rejected
        range looks exactly like a legitimately empty report - so check rather
        than trust.
        """
        summary = self.page.locator(c.DATE_SUMMARY_SELECTOR).first
        if not summary.count():
            return

        applied = summary.input_value()
        expected = (
            f"{start.strftime(c.DATE_INPUT_FORMAT)} - {end.strftime(c.DATE_INPUT_FORMAT)}"
        )
        if applied.strip() != expected:
            raise ReportExportError(
                f"Date range did not apply: asked for '{expected}', "
                f"the report shows '{applied}'."
            )

    def _wait_for_report_refresh(self) -> None:
        """
        Wait for the report to reload after a filter change.

        The table refreshes over XHR, so there is no navigation to wait on. A
        busy page is not an error here - the export step has its own waits.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=config.TIMEOUT)
        except PlaywrightTimeout:
            logger.debug("Report still busy after filtering; continuing anyway")

    # --- export --------------------------------------------------------

    def _export(self, report: Report) -> tuple[Download, str]:
        """
        Press Export and get the file back.

        Waits generously for the file, because a big report is slow to build
        rather than queued. Only when that wait is exhausted do we look at the
        Generated Reports page.
        """
        self._open_export_menu()
        self.page.wait_for_timeout(2000)  # the format menu animates open

        timeout = report.download_timeout_ms or c.DIRECT_DOWNLOAD_TIMEOUT_MS

        try:
            with self.page.expect_download(timeout=timeout) as download_info:
                ui.click_first_visible(
                    self.page, c.EXPORT_CSV_SELECTORS, "Export to CSV option"
                )
            return download_info.value, "download"

        except PlaywrightTimeout:
            logger.info(
                f"{report.label} did not download within {timeout // 1000}s - "
                "checking whether WellnessLiving queued it instead."
            )
            return self._download_from_generated_reports(report), "generated-reports"

    def _open_export_menu(self) -> None:
        """Open the Export menu, which hides behind Action on some layouts."""
        if ui.click_first_visible(
            self.page, c.EXPORT_BUTTON_SELECTORS, "Export button", required=False
        ):
            return

        ui.click_first_visible(self.page, c.ACTION_BUTTON_SELECTORS, "Action button")
        if not ui.click_first_visible(
            self.page, c.EXPORT_BUTTON_SELECTORS, "Export button", required=False
        ):
            raise ReportExportError(
                f"No Export control on {self.page.url}. Either the report has a "
                "different layout, or this staff role lacks the 'Export and "
                "print reports' permission."
            )

    def _download_from_generated_reports(self, report: Report) -> Download:
        """
        Collect a queued export from the Generated Reports page.

        WellnessLiving builds the file in the background and lists it there with
        a status; once it is ready, Action > Export to CSV hands it over.
        """
        deadline = time.monotonic() + config.DOWNLOAD_TIMEOUT / 1000

        while time.monotonic() < deadline:
            self.page.goto(c.GENERATED_REPORTS_URL, wait_until="domcontentloaded")
            self.page.wait_for_timeout(self.settle_ms)

            # An empty page means nothing was ever queued, so the export failed
            # for some other reason. Say so now instead of polling for minutes.
            if ui.first_visible(
                self.page, c.GENERATED_EMPTY_SELECTORS, "empty state", required=False
            ):
                raise ReportExportError(
                    f"'{report.label}' did not download, and WellnessLiving has "
                    "no queued report for it either. The export was probably "
                    "still building - try a narrower date range, or raise "
                    "DIRECT_DOWNLOAD_TIMEOUT_MS."
                )

            row = self._generated_row(report)

            if row is None:
                logger.debug(f"'{report.label}' not listed yet; waiting")
                self.page.wait_for_timeout(5000)
                continue

            # Whole-row text, since we do not yet know which cell holds the
            # status. Narrow this to the Status column during discovery.
            row_text = row.inner_text().strip().lower()

            if any(bad in row_text for bad in c.GENERATED_FAILED_STATUSES):
                raise ReportExportError(
                    f"WellnessLiving failed to generate '{report.label}'. "
                    "Try a narrower date range."
                )

            if any(good in row_text for good in c.GENERATED_READY_STATUSES):
                return self._download_generated_row(row, report)

            logger.debug(f"'{report.label}' still generating; waiting")
            self.page.wait_for_timeout(5000)

        raise ReportExportError(
            f"'{report.label}' was still generating after "
            f"{config.DOWNLOAD_TIMEOUT // 1000}s. It may finish later - the "
            "Generated Reports page keeps files for 90 days."
        )

    def _generated_row(self, report: Report):
        """The newest Generated Reports row for this report, if it is listed."""
        row = self.page.locator(c.GENERATED_ROW_SELECTOR).filter(
            has_text=report.label
        ).first
        return row if row.count() else None

    def _download_generated_row(self, row, report: Report) -> Download:
        """Export a finished Generated Reports row to CSV."""
        action = row.locator(",".join(c.ACTION_BUTTON_SELECTORS)).first
        if action.count():
            action.click()
        else:
            row.click()

        with self.page.expect_download(
            timeout=config.DOWNLOAD_TIMEOUT
        ) as download_info:
            ui.click_first_visible(
                self.page, c.EXPORT_CSV_SELECTORS, "Export to CSV option"
            )
        return download_info.value

    # --- saving --------------------------------------------------------

    def _save_raw(self, report: Report, download: Download) -> Path:
        """Save the download exactly as WellnessLiving produced it."""
        failure = download.failure()
        if failure:
            raise ReportExportError(f"Download failed: {failure}")

        suffix = Path(download.suggested_filename).suffix or ".csv"
        path = self.raw_dir / f"{report.key}{suffix}"
        download.save_as(path)
        logger.debug(f"Raw file: {path} ({path.stat().st_size} bytes)")
        return path

    def _normalise(self, report: Report, raw_path: Path) -> tuple[Path, int]:
        """
        Turn the raw export into a predictable CSV.

        Deliberately light for now: tidy headers, drop empty rows and columns.
        Per-report rules (date parsing, column renames, header assertions) get
        added once we have seen a real export from each report - see PLAN.md,
        step 7.
        """
        frame = pd.read_csv(raw_path, dtype=str, keep_default_na=False)

        frame.columns = [str(col).strip() for col in frame.columns]
        frame = frame.dropna(axis="columns", how="all")
        frame = frame.loc[:, [col for col in frame.columns if not col.startswith("Unnamed")]]
        frame = frame[~(frame == "").all(axis="columns")]

        self._check_headers(report, tuple(frame.columns))
        self._warn_if_truncated(report, frame)

        path = self.output_dir / f"{report.key}.csv"
        frame.to_csv(path, index=False, encoding="utf-8")
        return path, len(frame)

    def _warn_if_truncated(self, report: Report, frame: pd.DataFrame) -> None:
        """
        Warn when the data looks cut off at the start of our window.

        A history report whose earliest row falls on the very first day of the
        requested range is the signature of truncation, not of a business that
        happened to open that day. This was a real incident: a business whose
        `history_start` was set to 2024 from memory silently lost 22,481
        attendance rows going back to 2021, and the only clue was that the
        export began exactly on 1 January.
        """
        window = getattr(self, "_applied_window", None)
        if not window or report.date_mode != "past" or frame.empty:
            return

        start, _ = window
        date_columns = [col for col in frame.columns if "date" in col.lower()]
        if not date_columns:
            return

        values = pd.to_datetime(frame[date_columns[0]], errors="coerce").dropna()
        if values.empty:
            return

        earliest = values.min().date()
        if earliest <= start:
            logger.warning(
                f"{report.label}: earliest row is {earliest}, the first day of "
                f"the requested window. Data before {start} is probably being "
                f"cut off - check {self.business.slug}'s history_start."
            )

    def _check_headers(self, report: Report, headers: tuple[str, ...]) -> None:
        """
        Warn when an export's columns are not the ones we expect.

        WellnessLiving exports only the columns the staff account last chose
        under Action > Customize, so two businesses can silently hand back
        different shapes. Expected headers are filled in per report once we
        have seen a real file; until then this is a no-op.
        """
        if not report.expected_headers:
            logger.debug(f"Headers for {report.key}: {list(headers)}")
            return

        missing = [h for h in report.expected_headers if h not in headers]
        if missing:
            logger.warning(
                f"{report.label} is missing expected columns: {', '.join(missing)}. "
                "Check Action > Customize on this account."
            )

    # --- failure artefacts ---------------------------------------------

    def _capture_failure(self, report: Report) -> None:
        """
        Dump a screenshot and the page HTML so a failure can be diagnosed
        without reproducing it. Never let this mask the original error.
        """
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            self.page.screenshot(
                path=str(self.debug_dir / f"{report.key}-{stamp}.png"), full_page=True
            )
            (self.debug_dir / f"{report.key}-{stamp}.html").write_text(
                self.page.content(), encoding="utf-8"
            )
            logger.info(f"Failure artefacts written to {self.debug_dir}")
        except Exception as exc:
            logger.debug(f"Could not capture failure artefacts: {exc}")
