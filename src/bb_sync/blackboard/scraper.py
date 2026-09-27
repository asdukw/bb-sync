"""内容区抓取：课程列表、左菜单内容区、附件链接、公告。

Blackboard 经典版的几个关键事实（实测结论，勿轻易改动）:

- 课程列表从门户首页的 My Courses 模块取，DOM 查询 ``a[href*='launcher?type=Course']``
  比正则稳（链接里有 ``&amp;`` 编码坑）
- 课程首页 launcher 会跳到 ``modulepage/view``，左菜单里 ``listContent.jsp`` 才是
  真实内容区；``launchLink.jsp`` 是讨论区/工具，不抓
- 附件一律是 ``bbcswebdav`` 链接
- 公告 URL 必须带 ``method=search&context=mybb&viewChoice=2``
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from bb_sync.blackboard.models import (
    _PORTAL_COURSES,
    _PORTAL_HOME,
    BASE,
    Course,
    DueItem,
    FileItem,
)
from bb_sync.core.output import Console

# ---------------------------------------------------------------- 文本工具


def match_category(text: str, keywords: dict) -> str | None:
    """命中关键词返回分类，未命中返回 None（由调用方决定默认值）。"""
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
    """分类兜底：未命中任何关键词归 lectures。"""
    return match_category(text, keywords) or "lectures"


_STOP_WORDS = {
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


def make_slug(course: Course) -> str:
    """课程代码 + 标题里的英文关键词，如 ``CSC5010_Artificial_Intelligence``。

    最多取 3 个非停用词、总长 ≤40，避免把 "Deep Learning Applications" 截成 "Deep"。
    """
    if not course.code:
        safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", course.title).strip("_")
        return safe[:40]
    rest = course.title.upper().replace(course.code.upper(), "", 1)
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", rest)
    keys: list[str] = []
    total = 0
    for w in words:
        if w.upper() in _STOP_WORDS:
            continue
        if total + len(w) + 1 > 40:
            break
        keys.append(w.strip("-").capitalize())
        total += len(w) + 1
        if len(keys) >= 3:
            break
    key = "_".join(keys)
    return f"{course.code}_{key}" if key else course.code


# ---------------------------------------------------------------- 页面抓取


def _course_links(page: Page, console: Console) -> list[tuple[str, str]]:
    """从「My Courses」取课程链接：优先 My Courses 标签页，再退回首标签页。"""
    for url in (_PORTAL_COURSES, _PORTAL_HOME):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)
            pairs = page.eval_on_selector_all(
                "a[href*='launcher?type=Course']",
                "els => els.map(e => [e.href, (e.innerText || '').trim()])",
            )
            if pairs:
                console.debug(f"[scan] 课程来源: {url}")
                return pairs
        except PWTimeout:
            continue
    return []


def scrape_courses(page: Page, include, console: Console) -> list[Course]:
    """抓取课程列表并按 ``include`` 过滤。"""
    console.step("[scan] 获取课程列表 ...")
    pairs = _course_links(page, console)

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

    summary = ", ".join(c.code or c.title for c in courses) or "(无)"
    console.log(f"[scan] 发现 {len(courses)} 门课程: {summary}")
    return courses


def collect_menu_links(page: Page, course: Course) -> list[tuple[str, str]]:
    """获取课程左侧菜单中的内容区链接，返回 ``[(url, 菜单名), ...]``。"""
    page.goto(
        f"{BASE}/webapps/blackboard/execute/launcher?type=Course&id={course.bb_id}",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    page.wait_for_timeout(2500)
    pairs = page.eval_on_selector_all(
        "a[href*='listContent.jsp']", "els => els.map(e => [e.href, (e.innerText || '').trim()])"
    )
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
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


def sanitize_filename(name: str) -> str:
    """把标题转成合法文件名（去非法字符、截断到 150 字符）。"""
    import unicodedata

    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name[:150] or "untitled"


_DUE_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")


def parse_due_date(text: str) -> date | None:
    """解析 Blackboard Due 文本里的 ``MM/DD/YY`` 或 ``MM/DD/YYYY`` 日期。"""
    match = _DUE_DATE_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_due_entries(entries: list[dict[str, str]], course: Course) -> list[DueItem]:
    """把浏览器提取的原始 Due 行整理成领域对象，并去掉跨区块重复项。"""
    items: list[DueItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        title = re.sub(r"\s+", " ", str(entry.get("title") or "")).strip()
        due_text = re.sub(r"\s+", " ", str(entry.get("due_text") or "")).strip()
        if not title:
            continue
        due_date = parse_due_date(due_text)
        key = (title, due_date.isoformat() if due_date else due_text)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            DueItem(
                course_id=course.bb_id,
                course_code=course.code,
                course_title=course.title,
                title=title,
                due_date=due_date,
                due_text=due_text,
            )
        )
    return items


def find_files(
    page: Page,
    url: str,
    area_name: str,
    depth: int,
    max_depth: int,
    keywords: dict,
    visited: set[str],
) -> list[FileItem]:
    """递归抓取内容页中的附件（bbcswebdav）与子文件夹。"""
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
        seen_sub: set[str] = set()
        for href, sub_name in subs:
            m = re.search(r"content_id=(_\d+_1)", href)
            if not m or m.group(1) in seen_sub:
                continue
            seen_sub.add(m.group(1))
            items += find_files(
                page, href, sub_name or area_name, depth + 1, max_depth, keywords, visited
            )
    return items


def scrape_due_items(page: Page, course: Course, console: Console) -> list[DueItem]:
    """抓取课程主页 To Do 模块中的逾期与待办项目。"""
    home_url = f"{BASE}/webapps/blackboard/execute/launcher?type=Course&id={course.bb_id}"
    resp = page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1200)
    if resp and resp.status >= 400:
        raise RuntimeError(f"课程主页返回 HTTP {resp.status}")

    entries = page.eval_on_selector_all(
        "#pastDueView li, #dueView li",
        """els => els
            .filter(e => e.querySelector('.due') && !e.querySelector('li'))
            .map(e => ({
                title: (e.querySelector('a:not(.cmimg)')?.innerText || '').trim(),
                due_text: (e.querySelector('.due')?.innerText || '').trim(),
            }))""",
    )
    items = parse_due_entries(entries, course)
    console.debug(f"[due] {course.code or course.title}: {len(items)} 项")
    return items


def scrape_announcements(
    page: Page, course: Course, course_dir: Path, dry_run: bool, console: Console
) -> int:
    """抓取公告写入 ``announcements.md``；返回写入条数（失败不阻塞主流程）。"""
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
        count = len(lines) - 1
        if count and not dry_run:
            course_dir.mkdir(parents=True, exist_ok=True)
            (course_dir / "announcements.md").write_text("\n".join(lines), encoding="utf-8")
            console.log(f"    公告 x{count} → announcements.md")
        return count
    except Exception as exc:  # 公告失败不阻塞主流程
        console.warn(f"公告抓取失败: {exc}")
        return 0


__all__ = [
    "categorize",
    "collect_menu_links",
    "find_files",
    "make_slug",
    "match_category",
    "parse_due_date",
    "parse_due_entries",
    "sanitize_filename",
    "scrape_announcements",
    "scrape_courses",
    "scrape_due_items",
]
