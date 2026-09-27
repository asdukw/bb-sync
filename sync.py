"""
bb-sync — Blackboard 课程资源自动同步
======================================
登录大学 Blackboard 教学平台（ADFS SSO），抓取课程列表，自动下载课件/作业/指导
到本地课程文件夹（lectures / assignments / tutorials）。

用法：
    bb-sync                     # 增量同步（无头模式，复用已保存的登录态）
    bb-sync --headed            # 有头模式（首次登录 / 需要人工过 MFA 时用）
    bb-sync --dry-run           # 只列出将要下载的文件，不实际下载
    bb-sync --course CSC5010    # 只同步指定课程
    bb-sync --root D:/courses   # 指定课程文件下载目录（默认 ~/courses）
"""

from __future__ import annotations

import argparse
import html
import importlib.metadata as metadata
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request
from urllib.parse import unquote

import yaml
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from creds import delete_credentials, env_file_exists, load_credentials, save_credentials
from paths import BB_SYNC_HOME, find_config_file
from steel_backend import (
    connect,
    create_session,
    detect_browser,
    ensure_server,
    release_session,
)
from steel_backend import (
    healthy as steel_healthy,
)
from steel_backend import (
    is_deployed as steel_deployed,
)

try:
    __version__ = metadata.version("bb-sync")
except metadata.PackageNotFoundError:  # 源码直跑时
    __version__ = "0.1.0"

BASE = "https://bb.cuhk.edu.cn"
# ADFS 页面元素选择器
ADFS_USER = "input[name='userNameInput'], #userNameInput"
ADFS_PASS = "input[name='PasswordInput'], #passwordInput"
ADFS_NEXT = "#nextButton, #submitButton"  # 第一页「下一步」
ADFS_SUBMIT = "#submitButton, #nextButton, span.submit"  # 密码页「登录」
LOGIN_URL = f"{BASE}/webapps/login/"

# ---------------------------------------------------------------- data types


@dataclass
class Course:
    bb_id: str  # e.g. _12345_1
    title: str  # 原始标题
    code: str = ""  # 提取的课程代码，如 CSC5010
    slug: str = ""  # 本地文件夹名

    def __post_init__(self) -> None:
        m = re.search(r"\b([A-Z]{3,4}\d{4}[A-Z]?)\b", self.title.upper())
        if m:
            self.code = m.group(1)


@dataclass
class FileItem:
    name: str | None
    url: str
    category: str  # lectures / assignments / tutorials / other
    folder_hint: str = ""  # 内容区路径提示


# ---------------------------------------------------------------- helpers


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# 首次运行自动生成的默认配置（与内置默认值一致，用户可按需修改）
DEFAULT_CONFIG = """\
# bb-sync 配置（首次运行自动生成，可按需修改）

# 课程资源根目录（支持 ~ 和相对路径）
root: ~/courses

# 同步哪些课程：all = 全部，或列表如 [CSC5010, DDA5002]
include: all

# 课程代码 -> 本地文件夹名 的显式映射（未列出的自动命名）
course_dirs: {}

# 内容归类关键词：先匹配 assignments，再 tutorials，其余归 lectures
keywords:
  assignments: ["assignment", "homework", "作业", "hw"]
  tutorials: ["tutorial", "lab", "实验", "指导", "recitation"]
  lectures: ["lecture", "课件", "讲义", "slides", "notes", "note"]

# 公告是否同步（写入课程目录 announcements.md）
announcements: true

# 内容子文件夹递归深度（1 = 只下内容区第一层）
max_depth: 3
"""


def prompt_credentials() -> tuple[str, str]:
    """交互式录入凭据并保存到系统钥匙串，返回 (学号, 密码)。密码不回显。"""
    import getpass

    print("[login] 未找到已保存的凭据，请录入（保存后无需重复输入）")
    student_id = input("学号（学生）/ 邮箱前缀（教职工）: ").strip()
    if not student_id:
        raise SystemExit("学号不能为空")
    password = getpass.getpass("密码（输入不回显）: ")
    if not password:
        raise SystemExit("密码不能为空")
    backend = save_credentials(student_id, password)
    print(f"凭据已保存到系统钥匙串（{backend}），明文不再落盘 ✓")
    return student_id, password


def prompt_save_credentials() -> int:
    """bb-sync --login：交互式录入凭据并存入系统钥匙串。"""
    prompt_credentials()
    if env_file_exists():
        print("[提示] 检测到 .env 文件，其优先级低于钥匙串；建议删除以免双份维护。")
    return 0


def match_category(text: str, keywords: dict) -> str | None:
    """命中关键词返回分类，未命中返回 None（由调用方决定默认值）"""
    t = (text or "").lower()
    for cat in ("assignments", "tutorials"):  # 优先级高的先匹配
        for kw in keywords.get(cat, []):
            if kw.lower() in t:
                return cat
    for kw in keywords.get("lectures", []):
        if kw.lower() in t:
            return "lectures"
    return None


def categorize(text: str, keywords: dict) -> str:
    return match_category(text, keywords) or "lectures"


def make_slug(course: Course) -> str:
    """课程代码 + 标题里的英文关键词，如 CSC5010_Artificial_Intelligence"""
    if not course.code:
        safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", course.title).strip("_")
        return safe[:40]
    rest = course.title.upper().replace(course.code.upper(), "", 1)
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", rest)
    stop = {
        "THE",
        "AND",
        "FOR",
        "OF",
        "IN",
        "WITH",
        "FROM",
        "INTO",
        "USING",
        "TO",
        "THEIR",
        "SEMESTER",
        "FALL",
        "SPRING",
        "SUMMER",
        "SECTION",
        "TERM",
        "PART",
    }
    keys: list[str] = []
    total = 0
    for w in words:
        if w.upper() in stop:
            continue
        if total + len(w) + 1 > 40:
            break
        keys.append(w.strip("-").capitalize())
        total += len(w) + 1
        if len(keys) >= 3:
            break
    key = "_".join(keys)
    return f"{course.code}_{key}" if key else course.code


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name[:150] or "untitled"


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- browser


def is_logged_in(page: Page) -> bool:
    try:
        page.goto(
            f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1",
            wait_until="domcontentloaded",
            timeout=30000,
        )
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


def do_login(page: Page, username: str, password: str, headed: bool) -> None:
    log("[login] 打开登录页 ...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    # 主按钮跳转 ADFS OAuth2
    page.wait_for_selector("input[name='login'], #login input.submit", timeout=10000)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("input[name='login']")

    # ADFS 登录页（分页式：先账号 → 提交 → 再密码；少数配置同页显示）
    page.wait_for_selector(ADFS_USER, timeout=20000)
    log("[login] 填写用户名 ...")
    page.fill(ADFS_USER, username)

    if not page.locator(ADFS_PASS).first.is_visible():
        # 第一页只有账号：点「下一步」翻到密码页
        try:
            page.click(ADFS_NEXT, timeout=8000)
        except PWTimeout:
            page.press(ADFS_USER, "Enter")
        page.wait_for_selector(ADFS_PASS, state="visible", timeout=20000)

    log("[login] 填写密码 ...")
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
        debug_png = BB_SYNC_HOME / "debug_login.png"
        debug_html = BB_SYNC_HOME / "debug_login.html"
        page.screenshot(path=str(debug_png), full_page=True)
        debug_html.write_text(page.content(), encoding="utf-8")
        log(f"[debug] 当前 URL: {page.url}")
        log(f"[debug] 已保存截图 {debug_png.name} 与页面源码 {debug_html.name}（{BB_SYNC_HOME}）")
        raise RuntimeError(
            "登录失败：请检查凭据（bb-sync --login 可重新录入），或是否有 MFA/验证码。\n"
            f"(当前 URL: {page.url})\n"
            "如有 MFA，请运行 bb-sync --headed 手动在浏览器里完成一次登录，"
            "登录态会保存在 ~/.bb-sync/.browser-profile/，之后无需再手动。"
        )
    log("[login] 登录成功 ✓")


def ensure_login(page: Page, headed: bool) -> None:
    if is_logged_in(page):
        log("[login] 已有有效登录态（复用 Steel profile）")
        return
    creds = load_credentials()
    if creds.student_id and creds.password:
        log(f"[login] 凭据来源: {creds.source}")
        username, password = creds.student_id, creds.password
    else:
        # 本地无凭据：交互式录入（密码不回显），保存后本次直接使用
        username, password = prompt_credentials()
    try:
        do_login(page, username, password, headed)
        return
    except RuntimeError:
        if not headed:
            raise  # 静默模式失败就直接报错，提示改用 --headed
        # 有头模式：可能需要人工过 MFA，留时间给用户在窗口里操作
        log("[login] 等待人工完成 MFA / 验证码（最多 120 秒）...")
        for _ in range(60):
            page.wait_for_timeout(2000)
            if is_logged_in(page):
                log("[login] 检测到登录成功 ✓")
                return
        raise


# ---------------------------------------------------------------- scraping


def _course_links(page: Page) -> list[tuple[str, str]]:
    """从「My Courses」取课程链接：优先 My Courses 标签页，再退回首标签页"""
    for url in (
        f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_25_1",  # My Courses
        f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1",  # 首页
    ):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)
            pairs = page.eval_on_selector_all(
                "a[href*='launcher?type=Course']",
                "els => els.map(e => [e.href, (e.innerText || '').trim()])",
            )
            if pairs:
                log(f"[scan] 课程来源: {url}")
                return pairs
        except PWTimeout:
            continue
    return []


def scrape_courses(page: Page, include) -> list[Course]:
    log("[scan] 获取课程列表 ...")
    pairs = _course_links(page)

    courses: list[Course] = []
    seen: set[str] = set()
    for href, title in pairs:
        m = re.search(r"id=(_\d+_1)", href)
        if not m:
            continue
        bb_id = m.group(1)
        if bb_id in seen:
            continue
        seen.add(bb_id)
        courses.append(Course(bb_id=bb_id, title=title or bb_id))

    if include and include != "all":
        wanted = {c.upper() for c in (include if isinstance(include, list) else [include])}
        courses = [c for c in courses if c.code and c.code.upper() in wanted]

    log(f"[scan] 发现 {len(courses)} 门课程: " + ", ".join(c.code or c.title for c in courses))
    return courses


def collect_menu_links(page: Page, course: Course) -> list[tuple[str, str]]:
    """获取课程左侧菜单中的内容区链接，返回 [(url, 菜单名), ...]

    课程首页 = launcher → 会跳转到 modulepage/view，左菜单里 listContent.jsp
    才是真实内容区（launchLink.jsp 是讨论区/工具等，不抓）。
    """
    page.goto(
        f"{BASE}/webapps/blackboard/execute/launcher?type=Course&id={course.bb_id}",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    page.wait_for_timeout(2500)
    pairs = page.eval_on_selector_all(
        "a[href*='listContent.jsp']", "els => els.map(e => [e.href, (e.innerText || '').trim()])"
    )
    seen, out = set(), []
    for href, name in pairs:
        m = re.search(r"content_id=(_\d+_1)", href)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        out.append((href, name or cid))
    return out


def find_files(
    page: Page,
    url: str,
    area_name: str,
    depth: int,
    max_depth: int,
    keywords: dict,
    visited: set[str],
) -> list[FileItem]:
    """递归抓取内容页中的附件（bbcswebdav）与子文件夹"""
    if depth > max_depth or url in visited:
        return []
    visited.add(url)
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1000)
        if resp and resp.status >= 400:
            return []
    except PWTimeout:
        return []

    items: list[FileItem] = []
    # 附件：优先级「文件名关键词 > 内容区名关键词」
    files = page.eval_on_selector_all(
        "a[href*='bbcswebdav']", "els => els.map(e => [e.href, (e.innerText || '').trim()])"
    )
    for href, name in files:
        cat = match_category(name, keywords) or match_category(area_name, keywords) or "lectures"
        items.append(
            FileItem(
                name=sanitize_filename(name) if name else None,
                url=href,
                category=cat,
                folder_hint=area_name,
            )
        )

    # 子文件夹：内容区里的 listContent.jsp 条目
    if depth < max_depth:
        subs = page.eval_on_selector_all(
            "a[href*='listContent.jsp']",
            "els => els.map(e => [e.href, (e.innerText || '').trim()])",
        )
        seen_sub = set()
        for href, sub_name in subs:
            m = re.search(r"content_id=(_\d+_1)", href)
            if not m or m.group(1) in seen_sub:
                continue
            seen_sub.add(m.group(1))
            items += find_files(
                page, href, sub_name or area_name, depth + 1, max_depth, keywords, visited
            )
    return items


def extract_area_name(content: str) -> str:
    # 面包屑：id="breadcrumb" / class="breadcrumbs" 里的最后一级
    m = re.search(r'id="breadcrumb".*?<li[^>]*>\s*<span[^>]*>(.*?)</span>', content, re.S | re.I)
    if m:
        return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.S | re.I)
    if m:
        return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    return ""


HW_NUM = re.compile(r"(?:hw|home\s*work|set|assignment)\D{0,3}(\d{1,2})", re.I)


def sniff_ext(body: bytes) -> str | None:
    """按文件头嗅探真实类型（服务器常返回 application/octet-stream）"""
    if body.startswith(b"%PDF"):
        return ".pdf"
    if body.startswith(b"PK\x03\x04"):
        return ".zip"
    if body.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    head = body[:400].lstrip()
    if head.startswith(b"{"):
        if b'"cells"' in body[:4000]:
            return ".ipynb"
        return ".json"
    if head.startswith(b"<?xml") or head.lower().startswith(b"<html"):
        return ".xml"
    if body.startswith(b"\x1f\x8b"):
        return ".gz"
    return None


def download_item(
    ctx_request,
    item: FileItem,
    course_dir: Path,
    dry_run: bool,
    index: dict[str, Path] | None = None,
) -> tuple[str, str]:
    cat_dir = course_dir / item.category
    # 作业按 hwN 归档，对齐已有目录习惯（assignments/hw1/…）
    if item.category == "assignments" and item.name:
        m = HW_NUM.search(item.name)
        if m:
            cat_dir = cat_dir / f"hw{int(m.group(1))}"
    cat_dir.mkdir(parents=True, exist_ok=True)

    # 文件名优先级：链接文本 > Content-Disposition > URL 末段
    name = (item.name or "").strip()
    resp = ctx_request.get(item.url, timeout=60000)
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    if not name or name.lower() in {"download", "untitled", "link"}:
        if m:
            name = unquote(m.group(1))
        else:
            name = sanitize_filename(unquote(item.url.split("?")[0].rsplit("/", 1)[-1]) or "file")
    # 无扩展名/无法判断的（很多条目标题就是 "Lec 01"），按 Content-Type、再按文件头补后缀
    body = resp.body()
    if not Path(name).suffix or Path(name).suffix.lower() in {".bin", ".exe", ".dat"}:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        guess = mimetypes.guess_extension(ctype) if ctype else None
        if guess in {None, ".bin", ".exe"}:
            guess = sniff_ext(body)
        if not guess:
            tail = unquote(item.url.split("?")[0].rsplit("/", 1)[-1])
            if "." in tail[-6:]:
                guess = "." + tail.rsplit(".", 1)[-1].lower()
        if guess:
            name = (name.rsplit(".", 1)[0] if Path(name).suffix else name) + guess

    # 与已有文件重名（不管在课程目录下哪一层）就跳过，避免重复下载
    already = (index or {}).get(name.lower())
    if not already and index and len(body) > 50_000:
        # 名称不同但实际是同一个文件（BB 标题 vs 你手工改过的名字）
        already = index.get(f"__size__{len(body)}{Path(name).suffix.lower()}")
    if already:
        try:
            rel = str(already.relative_to(course_dir)).replace("\\", "/")
        except ValueError:
            rel = already.name
        return ("exists", rel)

    target = cat_dir / sanitize_filename(name)

    if dry_run:
        return ("would-download", target.name)
    if target.exists():
        return ("exists", target.name)
    if len(body) == 0:
        return ("empty", target.name)
    target.write_bytes(body)
    return ("downloaded", target.name)


def scrape_announcements(page: Page, course: Course, course_dir: Path, dry_run: bool) -> None:
    try:
        page.goto(
            f"{BASE}/webapps/blackboard/execute/announcement?method=search"
            f"&context=mybb&viewChoice=2&course_id={course.bb_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(1000)
        content = page.content()
        # 经典版公告结构：ul.announcementList > li（标题 h3 + 正文）
        blocks = re.findall(
            r"<li[^>]*class=\"[^\"]*announcement[^\"]*\"[^>]*>(.*?)</li>", content, re.S | re.I
        )
        if not blocks:
            blocks = re.findall(
                r"<div[^>]*class=\"[^\"]*announcement[^\"]*\"[^>]*>(.*?)</div>",
                content,
                re.S | re.I,
            )
        lines = [f"# {course.title} — 公告\n"]
        for b in blocks:
            b = html.unescape(b)
            t = re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S | re.I)
            body = re.sub(r"<h3[^>]*>.*?</h3>", "", b, flags=re.S | re.I)
            title = html.unescape(re.sub(r"<[^>]+>", " ", t.group(1))).strip() if t else "(无标题)"
            text = html.unescape(re.sub(r"<[^>]+>", " ", body))
            text = re.sub(r"\s+", " ", text).strip()
            lines.append(f"## {title}\n\n{text}\n")
        if len(lines) > 1 and not dry_run:
            (course_dir / "announcements.md").write_text("\n".join(lines), encoding="utf-8")
            log(f"    公告 x{len(lines) - 1} → announcements.md")
    except Exception as e:  # 公告失败不阻塞主流程
        log(f"    [warn] 公告抓取失败: {e}")


# ---------------------------------------------------------------- doctor


def _tool_version(cmd: list[str]) -> str | None:
    """运行 `<tool> --version`，返回首行；失败返回 None。"""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6, encoding="utf-8", errors="ignore"
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0].strip() if lines and out.returncode == 0 else None


def cmd_doctor() -> int:
    """体检：逐项检查前置条件，打印 ✓/✗ 与修复提示。"""
    print(f"bb-sync {__version__} — doctor\n")
    results: list[tuple[bool, bool]] = []  # (ok, required)

    def check(ok: bool, label: str, detail: str = "", hint: str = "", required: bool = True):
        results.append((ok, required))
        print(f"[{'✓' if ok else '✗'}] {label}" + (f"  {detail}" if detail else ""))
        if not ok and hint:
            print(f"    → {hint}")

    # Python
    v = sys.version_info
    check(
        (v.major, v.minor) >= (3, 11),
        "Python",
        f"{v.major}.{v.minor}.{v.micro}（需要 ≥3.11，uv 安装 bb-sync 时会自动准备）",
    )

    # git
    gp = shutil.which("git")
    gv = _tool_version([gp, "--version"]) if gp else None
    check(
        bool(gv),
        "git",
        gv or "未找到",
        "bb-sync 从 GitHub 安装需要 git：winget install --id Git.Git -e",
    )

    # Node.js ≥22
    np = shutil.which("node")
    nv = _tool_version([np, "--version"]) if np else None
    node_ok = False
    if nv:
        m = re.match(r"v(\d+)", nv)
        node_ok = bool(m) and int(m.group(1)) >= 22
    check(
        node_ok,
        "Node.js",
        nv or "未找到",
        "浏览器后端需要 Node.js ≥22（自带 npm）：winget install OpenJS.NodeJS.LTS",
    )

    # npm
    npx = shutil.which("npm")
    npv = _tool_version([npx, "--version"]) if npx else None
    check(bool(npv), "npm", npv or "未找到", "npm 随 Node.js 一起安装，单独缺失请重装 Node.js")

    # 浏览器（Chrome / Edge）
    custom = os.environ.get("CHROME_EXECUTABLE_PATH")
    if custom and Path(custom).exists():
        check(True, "浏览器", f"自定义 {custom}")
    else:
        browser = detect_browser()
        if browser:
            name = "Edge" if "edge" in Path(browser).name.lower() else "Chrome"
            check(True, "浏览器", f"{name} ({browser})")
        else:
            check(
                False,
                "浏览器",
                "未检测到 Chrome/Edge",
                "安装 Google Chrome 或 Microsoft Edge；装在非默认位置时设置环境变量 CHROME_EXECUTABLE_PATH",
            )

    # 网络（GitHub 可达性：首次部署需下载 Steel 源码；代理抖动可能瞬时失败，重试降低误报）
    net_ok, net_err = False, ""
    for _ in range(3):
        try:
            req = request.Request("https://github.com", headers={"User-Agent": "bb-sync"})
            request.urlopen(req, timeout=6)
            net_ok = True
            break
        except error.HTTPError:  # 有响应即视为可达（如 301/403）
            net_ok = True
            break
        except Exception as e:
            net_err = str(e)
            time.sleep(1.5)
    check(
        net_ok,
        "网络",
        "github.com 可达" if net_ok else f"github.com 不可达 ({net_err})",
        "代理用户请确认系统代理已开启",
    )

    # 凭据
    try:
        c = load_credentials()
        if c.student_id and c.password:
            check(True, "凭据", f"已保存（{c.source}）", required=False)
        else:
            check(False, "凭据", "未配置", "运行 bb-sync --login 录入学号密码", required=False)
    except Exception as e:
        check(False, "凭据", f"读取失败: {e}", "运行 bb-sync --login 重新录入", required=False)

    # Steel 后端
    if steel_healthy():
        check(True, "Steel 后端", "服务端运行中", required=False)
    elif steel_deployed():
        check(True, "Steel 后端", "已部署（未运行，同步时自动启动）", required=False)
    else:
        check(
            False,
            "Steel 后端",
            "未部署",
            "首次同步时自动下载部署（依赖 Node.js 与网络），无需手动操作",
            required=False,
        )

    required_fail = sum(1 for ok, req in results if req and not ok)
    total_ok = sum(1 for ok, _ in results if ok)
    print(
        f"\n{total_ok}/{len(results)} 项通过"
        + ("，存在缺失项，请按提示修复" if required_fail else "")
    )
    return 1 if required_fail else 0


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="bb-sync", description="Blackboard 课程资源自动同步（ADFS SSO + Steel）"
    )
    ap.add_argument("--headed", action="store_true", help="有头模式（首次登录/MFA 用）")
    ap.add_argument("--dry-run", action="store_true", help="只列出文件，不下载")
    ap.add_argument("--course", action="append", help="只同步指定课程代码（可多次）")
    ap.add_argument(
        "--root",
        help="课程文件下载目录（默认 ~/courses，也可在 config.yaml 的 root 里配置；此项优先）",
    )
    ap.add_argument(
        "--config",
        help="配置文件路径（默认：当前目录 config.yaml，其次 ~/.bb-sync/config.yaml）",
    )
    ap.add_argument("--login", action="store_true", help="交互式录入凭据并保存到系统钥匙串后退出")
    ap.add_argument("--logout", action="store_true", help="从系统钥匙串删除已保存的凭据")
    ap.add_argument(
        "--doctor", action="store_true", help="体检：检查前置条件（git/Node/浏览器/网络等）"
    )
    ap.add_argument("--version", action="version", version=f"bb-sync {__version__}")
    args = ap.parse_args()

    if args.login:
        return prompt_save_credentials()
    if args.doctor:
        return cmd_doctor()
    if args.logout:
        if delete_credentials():
            print("已从系统钥匙串删除凭据 ✓")
        else:
            print("钥匙串中没有保存的凭据")
        return 0

    config_path = Path(args.config).expanduser() if args.config else find_config_file("config.yaml")
    if not config_path.exists():
        if args.config:  # 用户显式指定的配置文件不存在时直接报错
            raise SystemExit(f"未找到配置文件：{config_path}")
        # 未做任何配置：自动生成默认配置到 ~/.bb-sync/
        config_path = BB_SYNC_HOME / "config.yaml"
        BB_SYNC_HOME.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        log(f"[info] 已生成默认配置 {config_path}（可按需修改）")
    cfg = load_config(config_path)
    keywords = cfg.get("keywords", {})
    max_depth = int(cfg.get("max_depth", 3))

    if args.root:
        root = Path(args.root).expanduser()
        if not root.is_absolute():  # 相对路径以当前工作目录为基准
            root = (Path.cwd() / root).resolve()
    else:
        root = Path(cfg.get("root", "~/courses")).expanduser()
        if not root.is_absolute():  # 相对路径以配置文件所在目录为基准
            root = (config_path.parent / root).resolve()
    first_run = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    if first_run:
        log(f"[info] 首次运行：课程文件将下载到 {root}")
        log("       （如需修改，可用 --root 参数或 config.yaml 里的 root）")

    include = args.course if args.course else cfg.get("include", "all")
    if include != "all" and isinstance(include, list):
        pass
    explicit_dirs = cfg.get("course_dirs") or {}

    stats = {"downloaded": 0, "exists": 0, "would-download": 0, "empty": 0}
    # ---- Steel 后端：独立进程、静默无窗口、登录态持久化在 profile ----
    ensure_server(args.headed)
    session = create_session(args.headed)

    with sync_playwright() as p:
        browser, ctx, page = connect(p, session)
        log(f"[steel] CDP 已接入 (UA: {ctx.browser.version if ctx.browser else '?'}）")

        ensure_login(page, args.headed)
        log("[login] 登录态已保存（Steel profile）")

        courses = scrape_courses(page, include)
        if not courses:
            log("[scan] 未发现课程，退出。")
            return 1

        for course in courses:
            folder_name = explicit_dirs.get(course.code) or make_slug(course)
            course_dir = root / folder_name
            log(f"\n=== {course.code or '?'} | {course.title} → {folder_name}/ ===")
            if not args.dry_run:
                course_dir.mkdir(parents=True, exist_ok=True)

            if cfg.get("announcements", True):
                scrape_announcements(page, course, course_dir, args.dry_run)

            all_items: list[FileItem] = []
            visited: set[str] = set()
            menu = collect_menu_links(page, course)
            log("    内容区: " + (", ".join(n for _, n in menu) or "(无)"))
            for area_url, area_name in menu:
                all_items += find_files(page, area_url, area_name, 1, max_depth, keywords, visited)

            # 去重（同一文件可能出现在多个位置）
            uniq: dict[str, FileItem] = {}
            for it in all_items:
                uniq.setdefault(it.url, it)
            log(f"    发现 {len(uniq)} 个文件")

            # 课程目录下已有文件索引（含你手工整理的历史文件），用于去重
            index: dict[str, Path] = {}
            if course_dir.exists():
                for p in course_dir.rglob("*"):
                    if not p.is_file() or ".git" in p.parts or ".venv" in p.parts:
                        continue
                    index.setdefault(p.name.lower(), p)
                    if p.stat().st_size > 50_000:  # 大文件再按「体积+后缀」去重
                        index.setdefault(f"__size__{p.stat().st_size}{p.suffix.lower()}", p)

            for it in uniq.values():
                status, name = download_item(ctx.request, it, course_dir, args.dry_run, index)
                stats[status] += 1
                if status == "downloaded":
                    log(f"    ↓ [{it.category}] {name}")
                elif status == "would-download":
                    log(f"    ? [{it.category}] {name} (dry-run)")
                elif status == "exists":
                    # 首次运行也可能出现：同一附件挂在多个内容区（URL 不同但落盘路径相同）
                    log(f"    = [{it.category}] {name} (已存在，跳过)")
                elif status == "empty":
                    log(f"    ! [{it.category}] {name} 内容为空，跳过")

        ctx.close()
        browser.close()
    release_session(session)

    log("\n========== 汇总 ==========")
    if args.dry_run:
        log(f"待下载: {stats['would-download']} 个文件（未实际下载）")
    else:
        log(f"新下载 {stats['downloaded']} 个，已存在跳过 {stats['exists']} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
