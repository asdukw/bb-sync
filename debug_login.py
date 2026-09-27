"""分步骤调试登录流程：每一步都 dump URL / 可见文本 / 截图"""

import re

from playwright.sync_api import sync_playwright

from creds import load_credentials
from paths import BB_SYNC_HOME
from steel_backend import connect, create_session, ensure_server, release_session

_creds = load_credentials()
USER = _creds.student_id
PASS = _creds.password


def dump(page, tag: str):
    url = page.url
    txt = re.sub(r"\s+", " ", page.inner_text("body"))[:600]
    try:
        fields = page.evaluate(
            "() => Array.from(document.querySelectorAll('input')).map(i => ({"
            " id: i.id, name: i.name, type: i.type, val: i.value, shown: !!i.offsetParent }))"
        )
        shown = [f for f in fields if f["type"] != "hidden" or f["val"]]
        extra = "\n" + "\n".join(f"    [input] {f}" for f in shown)
    except Exception as e:
        extra = f"\n    (eval failed: {e})"
    page.screenshot(path=str(BB_SYNC_HOME / f"debug_{tag}.png"), full_page=True)
    (BB_SYNC_HOME / f"debug_{tag}.txt").write_text(
        f"URL: {url}\n{extra}\n\n{txt}", encoding="utf-8"
    )
    print(f"\n--- [{tag}] URL: {url}{extra}")
    print(f"    text: {txt[:400]}")


def main():
    ensure_server(False)
    session = create_session(False)
    with sync_playwright() as p:
        browser, ctx, page = connect(p, session)
        page.goto("https://bb.cuhk.edu.cn/webapps/login/", wait_until="domcontentloaded")
        dump(page, "01_bb_login")

        page.click("input[name='login']")
        page.wait_for_timeout(3000)
        dump(page, "02_adfs_user")

        page.fill("#userNameInput", USER)
        dump(page, "03_filled_user")
        page.click("#nextButton")
        page.wait_for_timeout(3000)
        dump(page, "04_after_user_submit")

        page.fill("#passwordInput", PASS)
        dump(page, "05_filled_pass")
        page.click("#submitButton")
        page.wait_for_timeout(5000)
        dump(page, "06_after_pass_submit")

        page.wait_for_timeout(5000)
        dump(page, "07_final")
        ctx.close()
        browser.close()
    release_session(session)


if __name__ == "__main__":
    main()
