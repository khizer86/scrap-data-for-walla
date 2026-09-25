"""
Copy a finished run into the business's Google Drive folder, for backup.

Drive is reached through Google Drive for Desktop, so the destination is an
ordinary folder: DRIVE_ROOT from .env joined with the business's
`drive_folder` from businesses.json. Each run lands in a dated subfolder,
<drive folder>/<run date>/, so earlier runs are never overwritten.

We copy after the run rather than writing straight into Drive. Drive syncs
files as they appear, and a CSV still being written is a half-file in Drive.

What gets copied is driven by the run manifest (run.json): the raw download of
every report that succeeded, then run.json itself, all flat in the dated
folder. Only raw goes to Drive - it is the one file we cannot get back, while
the cleaned CSVs can always be rebuilt from it. Cleaned CSVs, debug
screenshots and traces stay local. Reading the manifest rather than the
current run's results means `--backup-only` and a same-day re-run both push
every report that has succeeded today, not just the latest batch.

A backup problem never costs us the extraction - the local files are already
safe in outputs/. It is reported in the summary and the run exits non-zero.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from loguru import logger

import config
from businesses import Business

MANIFEST_NAME = "run.json"


@dataclass
class BackupResult:
    """What happened to one business's backup, for the summary."""

    slug: str
    status: str  # "ok" | "skipped" | "failed"
    destination: Path | None = None
    files: list[Path] = field(default_factory=list)
    message: str = ""


def drive_folder_for(business: Business) -> Path | None:
    """The business's Drive folder, or None if backup is not configured for it."""
    if not config.DRIVE_ROOT or not business.drive_folder:
        return None
    # An absolute drive_folder wins over DRIVE_ROOT - Path joining does that.
    return Path(config.DRIVE_ROOT) / business.drive_folder


def backup_business(business: Business, run_date: date) -> BackupResult:
    """Copy one business's run for `run_date` into its Drive folder."""
    folder = drive_folder_for(business)
    if folder is None:
        missing = "DRIVE_ROOT in .env" if not config.DRIVE_ROOT else "drive_folder in businesses.json"
        return BackupResult(business.slug, "skipped", message=f"no {missing}")

    # Not created on demand: a typo in drive_folder would otherwise quietly
    # grow a stray folder in the shared drive, and a missing G: drive would
    # look like a successful backup to a local folder.
    if not folder.is_dir():
        return BackupResult(
            business.slug,
            "failed",
            destination=folder,
            message=f"Drive folder not found: {folder} - is Google Drive running, and the folder created?",
        )

    source = business.output_dir(run_date.isoformat())
    manifest_path = source / MANIFEST_NAME
    if not manifest_path.exists():
        return BackupResult(business.slug, "skipped", message=f"nothing to back up in {source}")

    try:
        files = _files_to_copy(source, manifest_path)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return BackupResult(business.slug, "failed", message=f"unreadable {manifest_path}: {exc}")

    destination = folder / run_date.isoformat()
    copied: list[Path] = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for path in files:
            target = destination / path.name
            shutil.copy2(path, target)
            copied.append(target)
    except OSError as exc:
        return BackupResult(
            business.slug,
            "failed",
            destination=destination,
            files=copied,
            message=f"copy failed after {len(copied)} file(s): {exc}",
        )

    logger.info(f"[{business.slug}] backed up {len(copied)} file(s) to {destination}")
    return BackupResult(business.slug, "ok", destination=destination, files=copied)


def _files_to_copy(source: Path, manifest_path: Path) -> list[Path]:
    """The raw export of every successful report in the manifest, then the manifest."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    files: list[Path] = []
    for entry in manifest["reports"]:
        if entry.get("status") != "ok":
            continue
        if not entry.get("raw_path"):
            continue
        path = Path(entry["raw_path"])
        if not path.exists():
            logger.warning(f"{entry['report']}: {path} is in the manifest but missing on disk")
            continue
        if not path.is_relative_to(source):
            logger.warning(f"{entry['report']}: {path} is outside {source}, not backed up")
            continue
        files.append(path)

    # Last, so a Drive folder holding run.json holds everything it lists.
    files.append(manifest_path)
    return files
