"""
The list of businesses we extract for.

Each business has its own WellnessLiving login, so onboarding a new one is a
single entry in businesses.json - no code change. That file holds passwords and
is gitignored; businesses.example.json shows the shape.

If businesses.json does not exist we fall back to the single set of credentials
in .env, so a first run works before the file is ever created.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import config

BUSINESSES_FILE = Path(config.PROJECT_ROOT / "businesses.json")


class BusinessConfigError(RuntimeError):
    """businesses.json is missing, malformed, or names no usable business."""


@dataclass(frozen=True)
class Business:
    """One WellnessLiving account we have credentials for."""

    slug: str
    name: str
    email: str
    password: str
    enabled: bool = True
    notes: str = ""

    @property
    def session_file(self) -> Path:
        """Cached browser session, kept per business so logins never collide."""
        return config.SESSION_DIR / f"wellnessliving-{self.slug}.json"

    def output_dir(self, run_date: str) -> Path:
        """Where this business's files for a given run date belong."""
        return config.OUTPUT_DIR / self.slug / run_date

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


def slugify(value: str) -> str:
    """Turn a business name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "business"


def load_businesses(path: Path | None = None) -> list[Business]:
    """
    Read every business from businesses.json.

    Disabled businesses are included - filtering is the caller's job, so that
    `--business <slug>` can still target one that is disabled by default.
    """
    path = path or BUSINESSES_FILE

    if not path.exists():
        logger.info(f"No {path.name} found - falling back to credentials in .env")
        return [_business_from_env()]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BusinessConfigError(f"{path} is not valid JSON: {exc}") from exc

    # Accept either a bare list or {"businesses": [...]}.
    entries = raw.get("businesses", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise BusinessConfigError(f"{path} contains no businesses.")

    businesses = [_business_from_entry(entry, index) for index, entry in enumerate(entries)]

    duplicates = {b.slug for b in businesses if [x.slug for x in businesses].count(b.slug) > 1}
    if duplicates:
        raise BusinessConfigError(
            f"Duplicate slugs in {path}: {', '.join(sorted(duplicates))}. "
            "Slugs name the output folder and session file, so they must be unique."
        )

    return businesses


def select_businesses(slugs: list[str] | None = None, path: Path | None = None) -> list[Business]:
    """
    Resolve the businesses a run should touch.

    With no slugs, that is every enabled business. With slugs, exactly those -
    including disabled ones, since naming a business is an explicit choice.
    """
    businesses = load_businesses(path)

    if not slugs:
        enabled = [b for b in businesses if b.enabled]
        if not enabled:
            raise BusinessConfigError(
                "Every business is disabled. Set \"enabled\": true on at least one, "
                "or name one explicitly with --business."
            )
        return enabled

    by_slug = {b.slug: b for b in businesses}
    unknown = [slug for slug in slugs if slug not in by_slug]
    if unknown:
        raise BusinessConfigError(
            f"Unknown business: {', '.join(unknown)}. "
            f"Known slugs: {', '.join(sorted(by_slug)) or '(none)'}"
        )

    return [by_slug[slug] for slug in slugs]


# --- internals ---------------------------------------------------------


def _business_from_entry(entry: dict, index: int) -> Business:
    """Build one Business from a businesses.json entry, with clear errors."""
    if not isinstance(entry, dict):
        raise BusinessConfigError(f"Entry #{index + 1} in businesses.json is not an object.")

    name = (entry.get("name") or entry.get("slug") or "").strip()
    email = (entry.get("email") or "").strip()
    password = entry.get("password") or ""

    missing = [
        field
        for field, value in (("name", name), ("email", email), ("password", password))
        if not value
    ]
    if missing:
        label = name or email or f"entry #{index + 1}"
        raise BusinessConfigError(
            f"Business {label} is missing: {', '.join(missing)}. "
            f"See {config.PROJECT_ROOT / 'businesses.example.json'}"
        )

    return Business(
        slug=entry.get("slug") or slugify(name),
        name=name,
        email=email,
        password=password,
        enabled=bool(entry.get("enabled", True)),
        notes=entry.get("notes", ""),
    )


def _business_from_env() -> Business:
    """A single business built from WELLNESSLIVING_EMAIL / _PASSWORD."""
    email, password = config.get_credentials("wellnessliving")
    return Business(
        slug="default",
        name="Default (from .env)",
        email=email,
        password=password,
        notes="Created from .env because businesses.json does not exist.",
    )
