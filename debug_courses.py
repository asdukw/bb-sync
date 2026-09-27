"""探测登录后的课程列表来源：dump 门户页并尝试若干候选接口"""

import re

from playwright.sync_api import sync_playwright

import sync
from paths import BB_SYNC_HOME
from steel_backend import connect, create_session, ensure_server, release_session

PORTAL = "https://bb.cuhk.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"


def main():
    ensure_server(False)
    session = create_session(False)
    with sync_playwright() as p:
        browser, ctx, page = connect(p, session)
        sync.ensure_login(page, False)
        print("[ok] 已登录")

        page.goto(PORTAL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        html_text = page.content()
        (BB_SYNC_HOME / "debug_portal.html").write_text(html_text, encoding="utf-8")
        print(f"[dump] 门户页 {len(html_text)} 字节 → debug_portal.html")

        for pat in [
            r"type=Course&id=(_\d+_1)",
            r"course_id=(_\d+_1)",
            r"/webapps/blackboard/execute/launcher",
            r"courseMain",
            r"My Course|我的课程|课程",
        ]:
            found = re.findall(pat, html_text)
            print(f"    {pat}: {len(found)} 处 {found[:4]}")

        # 候选接口
        for label, url in [
            (
                "REST /learn/api/v1/users/me/courses",
                "https://bb.cuhk.edu.cn/learn/api/v1/users/me/courses?limit=50",
            ),
            ("REST /learn/api/v1/courses", "https://bb.cuhk.edu.cn/learn/api/v1/courses?limit=50"),
        ]:
            try:
                r = ctx.request.get(url)
                body = r.text()[:300]
                print(f"    [{label}] status={r.status} body={body[:200]}")
            except Exception as e:
                print(f"    [{label}] 失败: {e}")

        # 页面左侧课程导航（经典版在 #div_1_1 或 .courseListing 里）
        print("\n--- 页面可见课程链接 ---")
        links = page.eval_on_selector_all(
            "a",
            "els => els.filter(e => /course|Course/.test(e.href)).slice(0, 15)"
            ".map(e => e.href + ' | ' + e.innerText.trim())",
        )
        for link in links:
            print("   ", link[:140])

        ctx.close()
        browser.close()
    release_session(session)


if __name__ == "__main__":
    main()
