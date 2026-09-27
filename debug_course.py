"""探测单门课程的内容区结构（默认 CSC5010）"""

import re
import sys

from playwright.sync_api import sync_playwright

import sync
from paths import BB_SYNC_HOME
from steel_backend import connect, create_session, ensure_server, release_session

COURSE_ID = sys.argv[1] if len(sys.argv) > 1 else "_18482_1"  # CSC5010


def main():
    ensure_server(False)
    session = create_session(False)
    with sync_playwright() as p:
        browser, ctx, page = connect(p, session)
        sync.ensure_login(page, False)

        for label, url in [
            (
                "launcher",
                f"{sync.BASE}/webapps/blackboard/execute/launcher?type=Course&id={COURSE_ID}",
            ),
            (
                "navmenu",
                f"{sync.BASE}/webapps/blackboard/execute/launcher?type=Course&id={COURSE_ID}&url=@bbg-cp-courseNavMenu",
            ),
        ]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[{label}] goto 失败: {e}")
                continue
            html_text = page.content()
            (BB_SYNC_HOME / f"debug_course_{label}.html").write_text(html_text, encoding="utf-8")
            print(f"\n=== {label} === url={page.url} size={len(html_text)}")
            for pat in [
                "listContent.jsp",
                "bbcswebdav",
                "courseMenu",
                "paletteItem",
                "contentListItem",
                "/webapps/blackboard/content/",
            ]:
                print(f"    {pat}: {len(re.findall(re.escape(pat), html_text))} 处")

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
            print(f"    相关链接 {len(interesting)} 条，前 20 条：")
            for link in interesting[:20]:
                print("       ", link[:150])

        ctx.close()
        browser.close()
    release_session(session)


if __name__ == "__main__":
    main()
