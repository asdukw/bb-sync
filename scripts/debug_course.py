"""探测单门课程的内容区结构（默认 CSC5010）。

用法（从仓库根运行）::

    uv run python scripts/debug_course.py [_18482_1]
"""

from __future__ import annotations

import re
import sys

from playwright.sync_api import sync_playwright

from bb_sync.blackboard.login import ensure_login
from bb_sync.blackboard.models import BASE
from bb_sync.browser import steel
from bb_sync.core.output import Console
from bb_sync.paths import BB_SYNC_HOME

console = Console()
COURSE_ID = sys.argv[1] if len(sys.argv) > 1 else "_18482_1"  # CSC5010


def main() -> None:
    steel.ensure_server(console)
    session = steel.create_session(console=console)
    with sync_playwright() as p:
        browser, ctx, page = steel.connect(p, session)
        ensure_login(page, console)

        for label, url in [
            ("launcher", f"{BASE}/webapps/blackboard/execute/launcher?type=Course&id={COURSE_ID}"),
            (
                "navmenu",
                f"{BASE}/webapps/blackboard/execute/launcher?type=Course"
                f"&id={COURSE_ID}&url=@bbg-cp-courseNavMenu",
            ),
        ]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except Exception as exc:
                console.log(f"[{label}] goto 失败: {exc}")
                continue
            html_text = page.content()
            (BB_SYNC_HOME / f"debug_course_{label}.html").write_text(html_text, encoding="utf-8")
            console.log(f"\n=== {label} === url={page.url} size={len(html_text)}")
            for pat in [
                "listContent.jsp",
                "bbcswebdav",
                "courseMenu",
                "paletteItem",
                "contentListItem",
                "/webapps/blackboard/content/",
            ]:
                console.log(f"    {pat}: {len(re.findall(re.escape(pat), html_text))} 处")

            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href + ' || ' + (e.innerText||'').trim().slice(0,60))",
            )
            interesting = [
                link
                for link in links
                if re.search(
                    r"listContent|bbcswebdav|content/|courseMenu|CONTENT|/webapps/blackboard/",
                    link,
                )
            ]
            console.log(f"    相关链接 {len(interesting)} 条，前 20 条：")
            for link in interesting[:20]:
                console.log(f"        {link[:150]}")

        ctx.close()
        browser.close()
    steel.release_session(session, console)


if __name__ == "__main__":
    main()
