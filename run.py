"""
Run an extraction, for one business or all of them.

    uv run python run.py --list                      # show businesses and reports
    uv run python run.py                             # every enabled business
    uv run python run.py -b hiptwist                 # one business
    uv run python run.py -b hiptwist -r clients      # one report
    uv run python run.py --headed --trace            # watch it, and record a trace

Onboarding a new business is an entry in businesses.json - see
businesses.example.json. The first run for a business should use --headed, since
WellnessLiving usually emails a verification code for an unrecognised device;
once cleared, the session is cached and later runs can be headless.

This file is platform-agnostic. Everything WellnessLiving-specific lives in
platforms/wellnessliving/, and a second platform is a sibling package exposing
the same runner interface - see platforms/wellnessliving/runner.py.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from datetime import date

from loguru import logger

import config
from businesses import Business, BusinessConfigError, select_businesses

# Platform name -> the module implementing describe_reports() and run_business().
PLATFORMS = {
    "wellnessliving": "platforms.wellnessliving.runner",
}
DEFAULT_PLATFORM = "wellnessliving"

# Pause between businesses. Hammering the same host with back-to-back logins is
# the quickest way to get an account rate-limited.
PAUSE_BETWEEN_BUSINESSES_S = 5


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)

    platform = importlib.import_module(PLATFORMS[args.platform])

    known_reports = [key for key, _, _ in platform.describe_reports()]
    unknown = [key for key in (args.report or []) if key not in known_reports]
    if unknown:
        # Checked up front: finding this out mid-run would mean a browser has
        # already been launched and a login spent.
        logger.error(
            f"Unknown report: {', '.join(unknown)}. "
            f"Known reports: {', '.join(known_reports)}"
        )
        return 2

    try:
        businesses = select_businesses(args.business)
    except (BusinessConfigError, RuntimeError) as exc:
        logger.error(exc)
        return 2

    if args.list:
        list_businesses(businesses, platform)
        return 0

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    headless = False if args.headed else config.HEADLESS

    report_count = len(args.report) if args.report else len(platform.describe_reports())
    logger.info(
        f"Extracting {report_count} report(s) from {args.platform} for "
        f"{len(businesses)} business(es) as of {run_date}"
    )

    all_results: dict[str, list] = {}
    for index, business in enumerate(businesses):
        if index:
            time.sleep(PAUSE_BETWEEN_BUSINESSES_S)
        try:
            all_results[business.slug] = platform.run_business(
                business, args.report, run_date, headless, args.trace
            )
        except KeyError as exc:  # an unknown report key
            logger.error(exc)
            return 2

    print_summary(all_results)

    failed = sum(
        1 for results in all_results.values() for r in results if r.status != "ok"
    )
    return 1 if failed else 0


def list_businesses(businesses: list[Business], platform) -> None:
    """Print the configured businesses and the reports we would pull."""
    print(f"\n{len(businesses)} business(es):\n")
    for business in businesses:
        state = "enabled" if business.enabled else "disabled"
        cached = "session cached" if business.session_file.exists() else "no session yet"
        print(f"  {business.slug:<24} {business.name:<28} {state}, {cached}")
        if business.notes:
            print(f"  {'':<24} note: {business.notes}")

    rows = platform.describe_reports()
    print(f"\n{len(rows)} report(s) on {platform.PLATFORM_NAME}:\n")
    for key, label, span in rows:
        print(f"  {key:<24} {label:<52} {span}")
    print()


def print_summary(all_results: dict[str, list]) -> None:
    """A short table of what came back, so a run can be read at a glance."""
    print("\n--- Summary ---")
    for slug, results in all_results.items():
        print(f"\n{slug}")
        for result in results:
            if result.status == "ok":
                print(
                    f"  ok      {result.report_key:<24} "
                    f"{result.rows:>7} rows  {result.elapsed_s:>5.0f}s  "
                    f"({result.via})"
                )
            else:
                print(f"  FAILED  {result.report_key:<24} {result.error}")
    print()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract report data from a booking platform, per business.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-p",
        "--platform",
        default=DEFAULT_PLATFORM,
        choices=sorted(PLATFORMS),
        help=f"Which platform to extract from. Default: {DEFAULT_PLATFORM}.",
    )
    parser.add_argument(
        "-b",
        "--business",
        action="append",
        metavar="SLUG",
        help="Business slug from businesses.json. Repeatable. Default: all enabled.",
    )
    parser.add_argument(
        "-r",
        "--report",
        action="append",
        metavar="KEY",
        help="Report to extract. Repeatable. Default: all. See --list for the keys.",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Treat this as today, for date ranges and the output folder.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser. Needed the first time a business logs in.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Record a Playwright trace per business into the run's debug folder.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List configured businesses and reports, then exit.",
    )
    parser.add_argument("--log-level", default=config.LOG_LEVEL, help="Default: INFO.")
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), format="{time:HH:mm:ss} {level:<8} {message}")


if __name__ == "__main__":
    sys.exit(main())
