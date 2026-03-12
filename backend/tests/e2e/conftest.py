"""Playwright E2E 测试 fixtures。"""

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def browser():
    """Session 级别共享浏览器实例。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    """每个测试独立页面。"""
    page = browser.new_page()
    yield page
    page.close()
