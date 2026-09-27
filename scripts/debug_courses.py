"""探测登录后的课程列表来源：dump 门户页并尝试若干候选接口。

用法（从仓库根运行）::

    uv run python scripts/debug_courses.py
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from bb_sync.blackboard.login import ensure_login
from bb_sync.blackboard.models import BASE
from bb_sync.browser import steel
from bb_sync.core.output import Console
from bb_sync.paths import BB_SYNC_HOME

console = Console()
PORTAL = f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"


def main() -> None:
    steel.ensure_server(console)
    session = steel.create_session(console=console)
    with sync_playwright() as p:
        browser, ctx, page = steel.connect(p, session)
        ensure_login(page, console)
        console.success("已登录")

        page.goto(PORTAL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        html_text = page.content()
        (BB_SYNC_HOME / "debug_portal.html").write_text(html_text, encoding="utf-8")
        console.log(f"[dump] 门户页 {len(html_text)} 字节 → debug_portal.html")

        for pat in [
            r"type=Course&id=(_\d+_1)",
            r"course_id=(_\d+_1)",
            r"/webapps/blackboard/execute/launcher",
            r"courseMain",
            r"My Course|我的课程|课程",
        ]:
            found = re.findall(pat, html_text)
            console.log(f"    {pat}: {len(found)} 处 {found[:4]}")

        # 候选接口
        for label, url in [
            (
                "REST /learn/api/v1/users/me/courses",
                f"{BASE}/learn/api/v1/users/me/courses?limit=50",
            ),
            ("REST /learn/api/v1/courses", f"{BASE}/learn/api/v1/courses?limit=50"),
        ]:
            try:
                r = ctx.request.get(url)
                console.log(f"    [{label}] status={r.status} body={r.text()[:200]}")
            except Exception as exc:
                console.log(f"    [{label}] 失败: {exc}")

        # 页面左侧课程导航（经典版在 #div_1_1 或 .courseListing 里）
        console.log("\n--- 页面可见课程链接 ---")
        links = page.eval_on_selector_all(
            "a",
            "els => els.filter(e => /course|Course/.test(e.href)).slice(0, 15)"
            ".map(e => e.href + ' | ' + e.innerText.trim())",
        )
        for link in links:
            console.log(f"    {link[:140]}")

        ctx.close()
        browser.close()
    steel.release_session(session, console)


if __name__ == "__main__":
    main()
