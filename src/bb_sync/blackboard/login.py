"""ADFS SSO 登录与登录态维护。

Blackboard 的登录按钮跳到 ADFS（STS）OAuth2；ADFS 登录页是**分页式**的：
第一页填账号 → 点「下一步」→ 第二页填密码 → 点「登录」。少数配置会显示在同一页，
所以这里两种形态都兼容。
"""

from __future__ import annotations

import contextlib
import getpass
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from bb_sync import paths
from bb_sync.blackboard.models import (
    _PORTAL_HOME,
    ADFS_NEXT,
    ADFS_PASS,
    ADFS_SUBMIT,
    ADFS_USER,
    LOGIN_URL,
)
from bb_sync.core.errors import AuthError
from bb_sync.core.output import Console
from bb_sync.creds import Credentials, load_credentials, save_credentials


def is_logged_in(page: Page) -> bool:
    """通过访问门户首页判断登录态是否有效。"""
    try:
        page.goto(_PORTAL_HOME, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        url = page.url
        if "login" in url.lower() or "adfs" in url.lower() or "sts." in url.lower():
            return False
        content = page.content()
        return (
            "user_id" not in content
            or "My Institutions" in content
            or "课程" in content
            or "Courses" in content
        )
    except PWTimeout:
        return False


def _safe_url(page: Page) -> str:
    """取当前 URL；页面已关闭等情况下退回占位文本，保证报错本身不二次失败。"""
    try:
        return page.url
    except Exception:
        return "(URL 不可用)"


def _dump_login_debug(page: Page) -> Path:
    """保存登录失败现场（截图 + 页面源码），返回产物目录；写盘失败不中断报错。"""
    home = paths.BB_SYNC_HOME
    with contextlib.suppress(OSError):
        home.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        page.screenshot(path=str(home / "debug_login.png"), full_page=True, timeout=5000)
    with contextlib.suppress(Exception):
        (home / "debug_login.html").write_text(page.content(), encoding="utf-8")
    return home


def do_login(page: Page, username: str, password: str) -> None:
    """执行一次完整登录；**任何一步**失败都保存调试产物并抛 :class:`AuthError`。

    登录链路上每一步都可能因站点改版、MFA、网络或选择器漂移而卡住。这里统一兜底：
    把当前 URL 写进错误信息，并落一份截图 + HTML，避免退化成「未预期错误 + 堆栈」
    这种既没有可操作提示、又没有现场可查的形态。
    """
    try:
        _perform_login(page, username, password)
    except Exception as exc:
        home = _dump_login_debug(page)
        detail = str(exc).strip() or exc.__class__.__name__
        raise AuthError(
            f"登录失败（当前 URL: {_safe_url(page)}）：{detail}",
            f"已保存登录现场到 {home}（debug_login.png / debug_login.html）。"
            "若页面停在 ADFS / MFA / 验证码，请用 bb-sync run --headed 手动完成；"
            "若是登录页改版导致选择器失效，请把 debug_login.html 反馈给开发者。",
        ) from exc


def _perform_login(page: Page, username: str, password: str) -> None:
    """实际登录流程（由 :func:`do_login` 统一兜底与落现场）。"""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    # 主按钮跳转 ADFS OAuth2
    page.wait_for_selector("input[name='login'], #login input.submit", timeout=10000)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("input[name='login']")

    # ADFS 登录页（分页式：先账号 → 提交 → 再密码；少数配置同页显示）
    page.wait_for_selector(ADFS_USER, timeout=20000)
    page.fill(ADFS_USER, username)

    if not page.locator(ADFS_PASS).first.is_visible():
        # 第一页只有账号：点「下一步」翻到密码页
        try:
            page.click(ADFS_NEXT, timeout=8000)
        except PWTimeout:
            page.press(ADFS_USER, "Enter")
        page.wait_for_selector(ADFS_PASS, state="visible", timeout=20000)

    page.fill(ADFS_PASS, password)
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.click(ADFS_SUBMIT, timeout=8000)
    except PWTimeout:
        page.press(ADFS_PASS, "Enter")
        page.wait_for_load_state("domcontentloaded", timeout=20000)

    # 等待回到 Blackboard
    for _ in range(30):
        page.wait_for_timeout(1000)
        if "bb.cuhk.edu.cn" in page.url and "adfs" not in page.url and "sts." not in page.url:
            break

    if not is_logged_in(page):
        raise AuthError("已填写凭据，但未回到 Blackboard（可能是账号/密码错误，或需要 MFA）")


def prompt_credentials(console: Console) -> Credentials:
    """交互式录入凭据并保存到系统钥匙串（密码不回显）。"""
    console.step("[login] 未找到已保存的凭据，请录入（保存后无需重复输入）")
    student_id = input("学号（学生）/ 邮箱前缀（教职工）: ").strip()
    if not student_id:
        raise AuthError("学号不能为空")
    password = getpass.getpass("密码（输入不回显）: ")
    if not password:
        raise AuthError("密码不能为空")
    backend = save_credentials(student_id, password)
    console.success(f"凭据已保存到系统钥匙串（{backend}），明文不再落盘")
    return Credentials(student_id, password, "keyring")


def resolve_credentials(console: Console) -> Credentials:
    """取得可用凭据：优先系统钥匙串，缺失时交互录入（并保存）。"""
    creds = load_credentials()
    if creds.ok:
        console.log(f"[login] 凭据来源: {creds.source}")
        return creds
    return prompt_credentials(console)


def ensure_login(page: Page, console: Console, *, headed: bool = False) -> None:
    """确保处于登录态（复用 profile → 静默登录 → 有头模式等人工过 MFA）。"""
    if is_logged_in(page):
        console.log("[login] 已有有效登录态（复用 Steel profile）")
        return

    creds = resolve_credentials(console)
    try:
        do_login(page, creds.student_id, creds.password)
    except AuthError:
        if not headed:
            raise  # 静默模式失败就直接报错，提示改用 --headed
        # 有头模式：可能需要人工过 MFA，留时间给用户在窗口里操作
        console.step("[login] 等待人工完成 MFA / 验证码（最多 120 秒）...")
        for _ in range(60):
            page.wait_for_timeout(2000)
            if is_logged_in(page):
                console.success("检测到登录成功")
                return
        raise
    console.success("登录成功")


__all__ = [
    "do_login",
    "ensure_login",
    "is_logged_in",
    "prompt_credentials",
    "resolve_credentials",
]
