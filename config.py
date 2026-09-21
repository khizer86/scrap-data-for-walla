"""
Shared configuration for all platform extractors.

Values come from environment variables (loaded from a local .env file),
with sensible defaults so the tool runs without any setup for most knobs.
Credentials have no defaults on purpose - they must be supplied.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root, if present.
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


# --- Paths -------------------------------------------------------------

# Every run drops its downloaded reports into a subfolder of this directory.
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT / "outputs"))

# Saved Playwright storage state (cookies + localStorage) per platform,
# so we can skip the login flow on subsequent runs while the session lasts.
SESSION_DIR = Path(os.getenv("SESSION_DIR", PROJECT_ROOT / ".sessions"))


# --- Browser behaviour -------------------------------------------------

# Headless by default; set HEADLESS=false while developing so you can watch
# the browser and complete any 2FA / verification prompts by hand.
HEADLESS = os.getenv("HEADLESS", "true").strip().lower() not in ("false", "0", "no")

# Slow down each Playwright action by N milliseconds (debugging aid).
SLOW_MO = int(os.getenv("SLOW_MO", "0"))

# Default timeout for navigation and element waits, in milliseconds.
TIMEOUT = int(os.getenv("TIMEOUT", "30000"))

# Longer timeout for report generation, which can take a while on big studios.
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "180000"))


# --- Logging -----------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# --- Credentials -------------------------------------------------------

# Per-platform credentials, read as <PLATFORM>_EMAIL / <PLATFORM>_PASSWORD.
def get_credentials(platform: str) -> tuple[str, str]:
    """
    Return (email, password) for a platform, e.g. get_credentials("wellnessliving")
    reads WELLNESSLIVING_EMAIL and WELLNESSLIVING_PASSWORD.

    Raises RuntimeError with a clear message if either is missing.
    """
    prefix = platform.upper()
    email = os.getenv(f"{prefix}_EMAIL")
    password = os.getenv(f"{prefix}_PASSWORD")

    missing = [
        name
        for name, value in ((f"{prefix}_EMAIL", email), (f"{prefix}_PASSWORD", password))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing credentials for {platform}: {', '.join(missing)}. "
            f"Add them to {PROJECT_ROOT / '.env'}"
        )

    return email, password
