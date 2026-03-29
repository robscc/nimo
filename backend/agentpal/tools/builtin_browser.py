"""浏览器 / 网页交互相关内置工具。"""

from __future__ import annotations

from agentscope.tool import ToolResponse

from agentpal.tools.builtin_fs import _text_response

# ── Playwright 可选导入（不存在时降级到 httpx）──────────────
try:
    from playwright.sync_api import sync_playwright

    USE_PLAYWRIGHT = True
except ImportError:
    USE_PLAYWRIGHT = False
    sync_playwright = None  # type: ignore[assignment]  # placeholder，供测试 mock


# ── 5. browser_use ────────────────────────────────────────


def browser_use(
    url: str,
    action: str = "get_text",
    selector: str = "",
    value: str = "",
    distance: int = 800,
    wait_ms: int = 1500,
) -> ToolResponse:
    """访问网页并与之交互（基于 Playwright 无头浏览器，支持 JS 渲染）。

    Args:
        url: 要访问的网页 URL
        action: 操作类型，支持：
            - "get_text"   提取 JS 渲染后的可见文字（默认）
            - "get_title"  获取页面 <title>
            - "get_html"   获取 <body> 外层 HTML（限 5000 字）
            - "screenshot" 截图保存至 /tmp，返回文件路径
            - "click"      点击 CSS 选择器匹配的元素（需 selector）
            - "fill"       向输入框填入文字（需 selector、value）
            - "scroll"     向下滚动页面（可选 distance，默认 800px）
        selector: CSS 选择器，用于 click / fill 操作
        value: 填入的文本内容，用于 fill 操作
        distance: 滚动距离（像素），用于 scroll 操作，默认 800
        wait_ms: 页面加载后额外等待毫秒数（等 JS 执行），默认 1500

    Returns:
        操作结果文本或文件路径
    """
    if USE_PLAYWRIGHT:
        return _browser_use_playwright(url, action, selector, value, distance, wait_ms)
    else:
        return _browser_use_httpx(url, action)


def _browser_use_playwright(
    url: str,
    action: str,
    selector: str,
    value: str,
    distance: int,
    wait_ms: int,
) -> ToolResponse:
    """Playwright 实现的 browser_use。

    sync_playwright() 内部会调用 asyncio.run()，若当前线程已有运行中的 event loop
    （FastAPI/uvicorn 场景）则直接冲突。解决方案：将 Playwright 操作提交到一个
    独立 worker 线程（该线程没有 event loop），主线程同步等待结果。
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _playwright_task, url, action, selector, value, distance, wait_ms
        )
        try:
            result = future.result(timeout=35)  # 比 goto timeout 多留余量
        except concurrent.futures.TimeoutError:
            return _text_response("<error>Playwright 操作超时（35s）</error>")
        except Exception as e:
            return _text_response(f"<error>Playwright 操作失败: {e}</error>")

    return _text_response(result)


def _playwright_task(
    url: str,
    action: str,
    selector: str,
    value: str,
    distance: int,
    wait_ms: int,
) -> str:
    """在 worker 线程中执行的 Playwright 操作，返回结果字符串或抛出异常。"""
    import time

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(wait_ms)

        if action == "get_text":
            text = page.inner_text("body")
            if len(text) > 5000:
                text = text[:5000] + "\n\n[...内容已截断...]"
            result = f"# {url}\n\n{text}"

        elif action == "get_title":
            result = f"页面标题: {page.title()}"

        elif action == "get_html":
            html = page.inner_html("body")
            if len(html) > 5000:
                html = html[:5000] + "\n\n[...内容已截断...]"
            result = f"# {url} HTML\n\n{html}"

        elif action == "screenshot":
            ts = int(time.time())
            path = f"/tmp/agentpal_screenshot_{ts}.png"
            page.screenshot(path=path)
            result = f"截图已保存: {path}"

        elif action == "click":
            if not selector:
                browser.close()
                raise ValueError("click 操作需要提供 selector 参数")
            page.click(selector)
            result = f"✅ 已点击元素: {selector}"

        elif action == "fill":
            if not selector:
                browser.close()
                raise ValueError("fill 操作需要提供 selector 参数")
            page.fill(selector, value)
            result = f"✅ 已填入内容到 {selector}"

        elif action == "scroll":
            page.evaluate(f"window.scrollBy(0, {distance})")
            result = f"✅ 已向下滚动 {distance}px"

        else:
            browser.close()
            raise ValueError(
                f"不支持的 action: {action}，"
                "可用值: get_text / get_title / get_html / screenshot / click / fill / scroll"
            )

        browser.close()
        return result


def _browser_use_httpx(url: str, action: str) -> ToolResponse:
    """httpx 降级实现（不支持 JS 渲染，仅 get_text / get_title）。"""
    try:
        import re

        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AgentPal/0.1"
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        if action == "get_title":
            match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            title = match.group(1).strip() if match else "（无标题）"
            return _text_response(f"页面标题: {title}")

        # get_text（及其他不支持的 action 统一降级为 get_text）
        text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()
        if len(text) > 3000:
            text = text[:3000] + "\n\n[...内容已截断...]"
        return _text_response(
            f"[注意：Playwright 未安装，使用 httpx 降级模式，不支持 JS 渲染]\n# {url}\n\n{text}"
        )

    except Exception as e:
        return _text_response(f"<error>访问失败: {e}</error>")
