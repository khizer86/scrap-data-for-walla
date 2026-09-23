"""
WellnessLiving login via Playwright.

Usage:

    from platforms.wellnessliving.auth import WellnessLivingAuth

    with WellnessLivingAuth() as page:
        page.goto(...)   # page is already logged in

The session (cookies + localStorage) is cached on disk, so repeat runs skip
the login form entirely until the session expires.
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger
from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

import config
from platforms.wellnessliving import constants as c


class LoginError(RuntimeError):
    """Raised when we could not get to a logged-in state."""


class WellnessLivingAuth:
    """Owns the browser and gets us to an authenticated WellnessLiving page."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        headless: bool | None = None,
        session_file: Path | None = None,
    ):
        # Credentials default to WELLNESSLIVING_EMAIL / _PASSWORD from .env.
        if email and password:
            self.email, self.password = email, password
        else:
            self.email, self.password = config.get_credentials("wellnessliving")

        self.headless = config.HEADLESS if headless is None else headless
        self.session_file = session_file or (config.SESSION_DIR / "wellnessliving.json")

        # Set up in start(), torn down in close().
        self._playwright: Playwright | None = None
        self._browser = None
        self._context = None
        self.page: Page | None = None

    # --- lifecycle -----------------------------------------------------

    def __enter__(self) -> Page:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    def start(self) -> Page:
        """Launch the browser and return a logged-in page."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=config.SLOW_MO,
        )

        # Reuse a saved session if we have one.
        storage_state = str(self.session_file) if self.session_file.exists() else None
        if storage_state:
            logger.info(f"Reusing saved session: {self.session_file}")

        self._context = self._browser.new_context(
            storage_state=storage_state,
            accept_downloads=True,
        )
        self._context.set_default_timeout(config.TIMEOUT)
        self.page = self._context.new_page()

        if storage_state and self._session_is_valid():
            logger.success("Existing session is still valid - skipping login")
            return self.page

        # Saved session was missing or stale, so log in properly.
        self._login()
        self._save_session()
        return self.page

    def close(self) -> None:
        """Shut everything down, tolerating partial startup."""
        for closeable in (self._context, self._browser):
            if closeable:
                try:
                    closeable.close()
                except Exception as exc:  # already closed / browser crashed
                    logger.debug(f"Ignoring error during close: {exc}")
        if self._playwright:
            self._playwright.stop()
        self._context = self._browser = self._playwright = self.page = None

    # --- login ---------------------------------------------------------

    def _session_is_valid(self) -> bool:
        """
        Ask for the login page. If the session still holds, WellnessLiving
        sends us on to the back office instead of showing the form.
        """
        try:
            self.page.goto(c.LOGIN_URL, wait_until="domcontentloaded")
        except PlaywrightTimeout:
            logger.warning("Timed out checking the saved session")
            return False
        return self._looks_logged_in()

    def _login(self) -> None:
        """Fill in and submit the login form."""
        logger.info(f"Logging in as {self.email}")
        self.page.goto(c.LOGIN_URL, wait_until="domcontentloaded")

        email_box = self._first_visible(c.EMAIL_SELECTORS, "email field")
        password_box = self._first_visible(c.PASSWORD_SELECTORS, "password field")

        email_box.fill(self.email)
        password_box.fill(self.password)

        self._tick_remember_me()

        submit = self._first_visible(c.SUBMIT_SELECTORS, "submit button", required=False)
        if submit:
            submit.click()
        else:
            # Some builds have no obvious submit button - Enter works instead.
            password_box.press("Enter")

        self._wait_for_login_result()

    def _tick_remember_me(self) -> None:
        """
        Tick "keep me signed in" so the cached session survives longer.

        The real checkbox is visually hidden behind a styled label, so
        Playwright's check() refuses it - we set it in the DOM instead. This is
        only an optimisation, so any failure is logged and shrugged off.
        """
        for selector in c.REMEMBER_ME_SELECTORS:
            checkbox = self.page.locator(selector).first
            if not checkbox.count():
                continue
            try:
                checkbox.evaluate(
                    "el => { if (!el.checked) { el.checked = true; "
                    "el.dispatchEvent(new Event('change', {bubbles: true})); } }"
                )
                logger.debug(f"Ticked remember-me via: {selector}")
            except Exception as exc:
                logger.debug(f"Could not tick remember-me ({selector}): {exc}")
            return

    def _wait_for_login_result(self) -> None:
        """Block until we are logged in, or fail with a useful message."""
        try:
            self.page.wait_for_load_state("networkidle")
        except PlaywrightTimeout:
            pass  # a busy page is fine; the checks below are what matter

        if self._looks_logged_in():
            logger.success(f"Logged in - landed on {self.page.url}")
            return

        # Passport redirects unrecognised addresses into its signup flow.
        if any(m in self.page.url for m in c.UNKNOWN_ACCOUNT_URL_MARKERS):
            raise LoginError(
                f"WellnessLiving does not recognise {self.email} - it offered the "
                "signup page instead of logging in. Check WELLNESSLIVING_EMAIL."
            )

        # WellnessLiving emails a verification code when it sees a new device,
        # and shows a captcha after repeated failed attempts. Neither can be
        # solved automatically, so both need a visible browser.
        blocker = self._first_visible(
            c.VERIFICATION_SELECTORS + c.CAPTCHA_SELECTORS,
            "verification / captcha field",
            required=False,
        )
        if blocker:
            if self.headless:
                raise LoginError(
                    "WellnessLiving asked for a verification code or captcha, which "
                    "cannot be completed headlessly. Re-run with HEADLESS=false, "
                    "finish it in the browser, and the session will be cached for "
                    "future runs."
                )
            logger.warning(
                "Verification required - complete it in the browser window. "
                f"Waiting up to {config.DOWNLOAD_TIMEOUT // 1000}s..."
            )
            self._wait_until_logged_in(config.DOWNLOAD_TIMEOUT)
            logger.success(f"Verification accepted - landed on {self.page.url}")
            return

        message = self._page_error() or "(no error message found on the page)"
        raise LoginError(f"Login failed. Still at {self.page.url}. Page said: {message}")

    def _wait_until_logged_in(self, timeout_ms: int) -> None:
        """Poll until the login form is behind us, or give up."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self._looks_logged_in():
                return
            self.page.wait_for_timeout(1000)
        raise LoginError(
            f"Gave up waiting for login to complete. Still at {self.page.url}."
        )

    def _save_session(self) -> None:
        """Cache cookies + localStorage so the next run can skip the login form."""
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self.session_file))
        logger.info(f"Session saved to {self.session_file}")

    # --- helpers -------------------------------------------------------

    def _looks_logged_in(self) -> bool:
        """
        Decide whether we are authenticated.

        We need a *positive* signal, not just the absence of a login form:
        passport pushes unknown addresses into a signup page that has no form
        either, and the public marketing site shares the back office's host.

        So we require three things: the back office host, no trace of the login
        flow in the URL, and an actual back-office link on the page.
        """
        url = self.page.url
        if not url.startswith(c.AUTHENTICATED_URL_PREFIX):
            return False
        if any(marker in url for marker in c.LOGIN_URL_MARKERS):
            return False

        password_box = self.page.locator(c.PASSWORD_SELECTORS[0]).first
        if password_box.count() and password_box.is_visible():
            return False

        # The dashboard is full of /rs/ links; the marketing site has none.
        return any(
            self.page.locator(selector).count()
            for selector in c.BACKOFFICE_MARKER_SELECTORS
        )

    def _first_visible(self, selectors, description: str, required: bool = True):
        """
        Return a locator for the first selector in `selectors` that is visible.

        Selectors are candidates rather than a single known-good value, because
        the login screen varies between WellnessLiving builds.
        """
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=3000)
                logger.debug(f"Found {description} via: {selector}")
                return locator
            except PlaywrightTimeout:
                continue

        if required:
            raise LoginError(
                f"Could not find the {description} on {self.page.url}. "
                f"Tried: {', '.join(selectors)}"
            )
        return None

    def _page_error(self) -> str | None:
        """Pull a visible error message off the page, if there is one."""
        error = self._first_visible(c.ERROR_SELECTORS, "error message", required=False)
        return error.inner_text().strip() if error else None


if __name__ == "__main__":
    # Smoke test:  uv run python -m platforms.wellnessliving.auth
    # Run it with HEADLESS=false the first time so you can watch the flow
    # and clear any verification prompt.
    import sys

    logger.remove()
    logger.add(sys.stderr, level=config.LOG_LEVEL)

    with WellnessLivingAuth() as page:
        logger.info(f"Landed on: {page.url}")
        logger.info(f"Page title: {page.title()}")
        page.screenshot(path="login-check.png", full_page=True)
        logger.success("Screenshot written to login-check.png")
