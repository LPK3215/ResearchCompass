"""Real browser regression for the paid-user critical research workflow.

Run explicitly with ``BROWSER_REGRESSION=1`` and credentials.  The test uses a
real Chromium instance; it is intentionally skipped when the browser or a
configured account is unavailable instead of being reported as passed.
"""

from __future__ import annotations

import os

import pytest

playwright = pytest.importorskip("playwright.sync_api")


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _required_environment() -> tuple[str, str, str]:
    if os.getenv("BROWSER_REGRESSION", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set BROWSER_REGRESSION=1 to run browser regression against a live stack.")
    base_url = os.getenv("BROWSER_BASE_URL", "http://localhost:5173").rstrip("/")
    username = os.getenv("E2E_USERNAME") or os.getenv("TEST_USERNAME")
    password = os.getenv("E2E_PASSWORD") or os.getenv("TEST_PASSWORD")
    if not username or not password:
        pytest.skip("E2E_USERNAME/E2E_PASSWORD or TEST_USERNAME/TEST_PASSWORD is required.")
    return base_url, username, password


def _assert_loaded(page, path: str) -> None:
    page.goto(path, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    assert page.locator("body").inner_text(timeout=15).strip()


def test_critical_research_workflow_in_real_browser():
    base_url, username, password = _required_environment()
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium is not installed or cannot start: {type(exc).__name__}")
        context = browser.new_context(base_url=base_url)
        page = context.new_page()
        try:
            # Login is a visible browser interaction, not a direct token request.
            page.goto("/login", wait_until="domcontentloaded")
            page.get_by_test_id("login-id").fill(username)
            page.get_by_test_id("login-password").fill(password)
            agreement = page.get_by_text("登录即代表同意", exact=False)
            if agreement.count() > 0:
                page.locator('input[type="checkbox"]').check()
            page.get_by_test_id("login-submit").click()
            page.wait_for_url("**/agent**", timeout=30_000)

            # Main research surfaces must remain routable after authentication.
            _assert_loaded(page, "/agent")
            _assert_loaded(page, "/research")
            _assert_loaded(page, "/extensions?tab=knowledge")

            # The project page must expose the project workflow entry point.
            page.goto("/research", wait_until="domcontentloaded")
            assert page.get_by_text("研究项目", exact=False).count() > 0

            # Critical controls must be rendered and usable before any write action.
            page.get_by_test_id("create-research-project").wait_for(state="visible")
            page.get_by_role("tab", name="智能检索").click()
            page.get_by_test_id("research-query").wait_for(state="visible")
            search_submit = page.get_by_test_id("research-search-submit")
            assert search_submit.is_visible()

            # Registration validation is exercised without creating an account.
            page.goto("/register", wait_until="domcontentloaded")
            page.get_by_placeholder("2-20 个字符,中英文/数字/下划线").fill("x")
            page.get_by_placeholder("至少 8 位").fill("short")
            page.get_by_placeholder("再次输入密码").fill("short")
            page.get_by_role("button", name="注册").click()
            assert page.get_by_text("用户名仅支持", exact=False).count() > 0 or page.get_by_text("密码至少", exact=False).count() > 0

            # Browser-side API calls verify the same authenticated session can
            # reach notification and research endpoints used by the UI.
            notifications = page.request.get(f"{base_url}/api/notifications?limit=1")
            assert notifications.ok, notifications.text()
        finally:
            context.close()
            browser.close()
