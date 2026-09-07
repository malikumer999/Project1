"""Browser-backed, short-lived session management for public Upwork searches."""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Callable

SEARCH_URL = "https://www.upwork.com/nx/search/jobs/?q=python%20developer"
SEARCH_TOKEN_COOKIES = ("UniversalSearchNuxt_vt", "visitor_gql_token", "visitor_signup_gql_token")


def _installed_chrome_major_version(uc) -> int | None:
    """Return Chrome's major version so uc downloads a compatible driver."""
    configured_version = os.getenv("UPWORK_CHROME_MAJOR_VERSION")
    if configured_version:
        return int(configured_version)

    # On Windows Chrome is normally registered here, even when its executable
    # is not discoverable through PATH or undetected-chromedriver's lookup.
    if os.name == "nt":
        import winreg

        registry_locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
        )
        for hive, path in registry_locations:
            try:
                with winreg.OpenKey(hive, path) as key:
                    version, _ = winreg.QueryValueEx(key, "version")
                match = re.search(r"(\d+)\.", version)
                if match:
                    return int(match.group(1))
            except OSError:
                continue

    executable = uc.find_chrome_executable()
    if not executable:
        return None
    try:
        output = subprocess.check_output([executable, "--version"], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"(\d+)\.", output)
    return int(match.group(1)) if match else None


class UpworkSession:
    """Keep a browser visitor session and renew it before its opaque token is stale."""

    def __init__(self, refresh_after_seconds: int | None = None, clock: Callable[[], float] = time.time) -> None:
        # Upwork visitor tokens last about one hour; renew ten minutes early.
        self.refresh_after_seconds = refresh_after_seconds or int(os.getenv("UPWORK_SESSION_REFRESH_SECONDS", "3000"))
        self.token_wait_seconds = int(os.getenv("UPWORK_TOKEN_WAIT_SECONDS", "30"))
        # After a failed token refresh, retry every 30 seconds so the scraper
        # recovers quickly instead of waiting minutes between attempts.
        self.retry_delay_seconds = int(os.getenv("UPWORK_REFRESH_RETRY_SECONDS", "30"))
        self._clock = clock
        self.cookies: dict[str, str] = {}
        self.user_agent: str | None = None
        self.last_refresh: float | None = None
        self._retry_after: float | None = None

    def needs_refresh(self) -> bool:
        return not self.cookies or self.last_refresh is None or self._clock() - self.last_refresh >= self.refresh_after_seconds

# main function that refreshes the session by opening a browser and collecting cookies and user agent

    def refresh(self) -> None:
        """Open Chrome once, then collect fresh cookies and the user agent."""
        try:
            import undetected_chromedriver as uc
        except ImportError as exc:
            raise RuntimeError("Install dependencies with `pip install -r requirements.txt` to enable browser-session refresh.") from exc

    #   helps prevent opening chrome after rapid refresh attempts when Upwork rejects the visitor token

        if self._retry_after and self._clock() < self._retry_after:
            remaining = int(self._retry_after - self._clock())
            raise RuntimeError(f"Visitor token refresh will retry in {remaining} seconds.")

        print(f"[upwork] Refreshing browser session (waiting up to {self.token_wait_seconds}s for a token)...")
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1920,1080")
        # The browser uses an isolated automation profile. Skip Chrome's
        # onboarding/profile picker so navigation reaches Upwork immediately.
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-search-engine-choice-screen")
        options.add_argument("--disable-features=ProfilePickerOnStartup")
        chrome_major_version = _installed_chrome_major_version(uc)
        driver_options = {"options": options}
        if chrome_major_version:
            print(f"[upwork] Using ChromeDriver compatible with Chrome {chrome_major_version}.")
            driver_options["version_main"] = chrome_major_version
        try:
            driver = uc.Chrome(**driver_options)
        except KeyboardInterrupt:
            # Ctrl+C during driver download/launch: undetected-chromedriver
            # swallows the interrupt internally, so kill leftover processes
            # and let the interrupt propagate.
            subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
                           capture_output=True)
            raise
        try:
            driver.set_page_load_timeout(self.token_wait_seconds)
            print("[upwork] Opening Upwork in Chrome...")
            driver.get(SEARCH_URL)
            deadline = self._clock() + self.token_wait_seconds
            while self._clock() < deadline:


#   refreshed cookies
                cookies = {item["name"]: item["value"] for item in driver.get_cookies()}
                if any(cookies.get(name) for name in SEARCH_TOKEN_COOKIES):
                    self.cookies = cookies
                    self.user_agent = driver.execute_script("return navigator.userAgent;")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            # Ctrl+C while Chrome is navigating/polling: quit the browser so
            # nothing is left blocking, then let the interrupt propagate.
            try:
                driver.quit()
            except Exception:
                subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
                               capture_output=True)
            raise
        finally:
            try:
                driver.quit()
            except OSError as exc:
                if getattr(exc, "winerror", None) != 6:
                    raise
            finally:
                # undetected-chromedriver may call quit again from __del__.
                driver.quit = lambda: None
                driver = None

        if not self._visitor_token():
            self.cookies = {}
            self.user_agent = None

#    pause the refresh attempts for a while when Upwork rejects the visitor token

            self._retry_after = self._clock() + self.retry_delay_seconds
            raise RuntimeError(
                "Upwork did not provide a visitor GraphQL token. "
                f"Browser refreshes are paused for {self.retry_delay_seconds} seconds."
            )
        self.last_refresh = self._clock()
        self._retry_after = None
        print("[upwork] Session refreshed.")

    def _visitor_token(self) -> str | None:
        return next((self.cookies[name] for name in SEARCH_TOKEN_COOKIES if self.cookies.get(name)), None)

    def get_session(self, force_refresh: bool = False) -> tuple[dict[str, str], str]:
        if force_refresh or self.needs_refresh():
            self.refresh()
        if not self.user_agent:
            raise RuntimeError("Upwork session is missing a user agent.")
        return self.cookies.copy(), self.user_agent

    def get_headers(self, force_refresh: bool = False) -> tuple[dict[str, str], dict[str, str]]:
        """Return headers/cookies with the current dynamic visitor token."""
        cookies, user_agent = self.get_session(force_refresh=force_refresh)
        token = self._visitor_token()
        if not token:
            if force_refresh:
                raise RuntimeError("Upwork session is missing its visitor GraphQL token.")
            # The stored session has no usable token: refresh right away
            # instead of waiting out the normal ~50-minute session lifetime.
            return self.get_headers(force_refresh=True)
        return {
            "Accept": "*/*", "Content-Type": "application/json", "Origin": "https://www.upwork.com",
            "Referer": SEARCH_URL, "User-Agent": user_agent, "Authorization": f"Bearer {token}",
            "x-upwork-accept-language": "en-US",
        }, cookies
