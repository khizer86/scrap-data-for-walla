"""
Small Playwright helpers shared across the WellnessLiving modules.

The one idea here: selectors are *candidates*, not certainties. WellnessLiving's
markup differs between builds and between businesses, so every lookup takes a
tuple of selectors and uses the first one that is actually visible. A markup
change then degrades into a fallback instead of a hard failure.
"""

from __future__ import annotations

from loguru import logger
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout


class ElementNotFound(RuntimeError):
    """None of the candidate selectors matched a visible element."""


def first_visible(
    page: Page,
    selectors: tuple[str, ...],
    description: str,
    required: bool = True,
    timeout: int = 3000,
) -> Locator | None:
    """
    Return a locator for the first selector in `selectors` that is visible.

    Raises ElementNotFound when nothing matches and `required` is True;
    otherwise returns None so the caller can take a different route.
    """
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout)
            logger.debug(f"Found {description} via: {selector}")
            return locator
        except PlaywrightTimeout:
            continue

    if required:
        raise ElementNotFound(
            f"Could not find the {description} on {page.url}. "
            f"Tried: {', '.join(selectors)}"
        )
    return None


def click_first_visible(
    page: Page,
    selectors: tuple[str, ...],
    description: str,
    required: bool = True,
    timeout: int = 3000,
) -> bool:
    """Click the first visible candidate. Returns whether anything was clicked."""
    locator = first_visible(page, selectors, description, required, timeout)
    if locator is None:
        return False
    locator.click()
    return True


def visible_text(
    page: Page, selectors: tuple[str, ...], description: str
) -> str | None:
    """Return the trimmed text of the first visible candidate, if any."""
    locator = first_visible(page, selectors, description, required=False)
    return locator.inner_text().strip() if locator else None
