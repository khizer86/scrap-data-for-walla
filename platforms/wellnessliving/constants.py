"""
WellnessLiving constants: URLs, selectors and report identifiers.

Selectors were read off the live login page (2026-09-21). Each one is a list
of candidates - the first that is actually visible wins - so a markup change
degrades into a fallback instead of a hard failure.
"""

# --- URLs --------------------------------------------------------------

BASE_URL = "https://us.wellnessliving.com"

# Staff / business login. This redirects to the shared "passport" login on
# www.wellnessliving.com with region parameters attached, then back to the
# regional site once authenticated.
LOGIN_URL = f"{BASE_URL}/login"

# Login, registration and 2FA all happen on the shared "passport" host
# (www.wellnessliving.com). Being handed back to the regional host is
# therefore our positive signal that authentication succeeded - we cannot
# rely on a known dashboard path, since WellnessLiving 404s unknown paths
# instead of redirecting to login.
AUTHENTICATED_URL_PREFIX = BASE_URL

# URL fragments that mean we are still somewhere in the login flow.
LOGIN_URL_MARKERS = (
    "/login",
    "/signin",
    "/sign-in",
    "/passport/",
)

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


# --- Reports -----------------------------------------------------------
# Filled in when we build reports.py. The API path (WlReportSid) is parked
# here too, for when the WellnessLiving app auth codes arrive.
REPORTS: dict[str, dict] = {}
