"""
Runs a WellnessLiving extraction for one business.

This is the platform's entry point. `run.py` at the project root knows nothing
about WellnessLiving beyond the two functions here, so a second platform is a
sibling module with the same two functions - not a change to the CLI.

The contract a platform runner implements:

    describe_reports()  -> rows for `run.py --list`
    run_business(...)   -> a list of results, one per report
"""

from __future__ import annotations

import json
import time
from datetime import date

from loguru import logger

from businesses import Business
from platforms.wellnessliving.auth import LoginError, WellnessLivingAuth
from platforms.wellnessliving.extractor import ExportResult, WellnessLivingExtractor
from platforms.wellnessliving.reports import REPORTS, get_reports

PLATFORM_NAME = "wellnessliving"


def describe_reports() -> list[tuple[str, str, str]]:
    """(key, label, window) for every report, for `--list`."""
    rows = []
    for key, report in REPORTS.items():
        window = report.date_range()
        span = f"{window[0]} to {window[1]}" if window else "no date range"
        rows.append((key, report.label, span))
    return rows


def run_business(
    business: Business,
    report_keys: list[str] | None,
    run_date: date,
    headless: bool,
    trace: bool,
) -> list[ExportResult]:
    """
    Extract every requested report for one business.

    A business that cannot log in is reported and skipped - the others still
    run, since one expired password should not cost us the whole batch.
    """
    reports = get_reports(report_keys)
    logger.info(f"=== {business} ===")

    auth = WellnessLivingAuth(
        email=business.email,
        password=business.password,
        headless=headless,
        session_file=business.session_file,
    )

    try:
        page = auth.start()
    except LoginError as exc:
        logger.error(f"[{business.slug}] login failed - {exc}")
        return [
            ExportResult(
                report_key=report.key,
                label=report.label,
                status="failed",
                error=f"login failed: {exc}",
            )
            for report in reports
        ]

    extractor = WellnessLivingExtractor(page, business, run_date)

    if trace:
        extractor.debug_dir.mkdir(parents=True, exist_ok=True)
        page.context.tracing.start(screenshots=True, snapshots=True, sources=True)

    try:
        results = extractor.extract_all(reports)
    finally:
        if trace:
            trace_path = extractor.debug_dir / "trace.zip"
            page.context.tracing.stop(path=str(trace_path))
            logger.info(f"Trace written to {trace_path} - playwright show-trace it")
        auth.close()

    _write_manifest(business, extractor, results, run_date)
    return results


def _write_manifest(
    business: Business,
    extractor: WellnessLivingExtractor,
    results: list[ExportResult],
    run_date: date,
) -> None:
    """
    Record what this run produced, next to the files it produced.

    Merged rather than overwritten: re-running two failed reports must not
    erase the record of the eight that succeeded earlier the same day, when
    their files are still sitting in the same folder.
    """
    extractor.output_dir.mkdir(parents=True, exist_ok=True)
    path = extractor.output_dir / "run.json"

    reports: dict[str, dict] = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            reports = {entry["report"]: entry for entry in previous.get("reports", [])}
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(f"Ignoring unreadable manifest at {path}: {exc}")

    for result in results:
        reports[result.report_key] = result.as_dict()

    manifest = {
        "platform": PLATFORM_NAME,
        "business": {"slug": business.slug, "name": business.name},
        "run_date": run_date.isoformat(),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reports": list(reports.values()),
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info(f"Manifest: {path}")
