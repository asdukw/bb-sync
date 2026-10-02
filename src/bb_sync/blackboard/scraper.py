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

import re
import time
from contextlib import suppress
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


def _wait_for_content(page: Page, selector: str, timeout_ms: int, *, settle_ms: int = 300) -> None:
    """等待关键内容渲染，最多不超过原来的固定等待时间。

    原实现用固定 ``wait_for_timeout`` 盲等：快页面浪费时间，慢页面又可能不够。
    改为等目标选择器出现，再等网络空闲以排除「只渲染了一半」的窗口；选择器未出现（空列表等）
    时等满 ``timeout_ms`` 后继续，与原来的固定等待等价，不会更慢。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
    except PWTimeout:
        return
    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
    if remaining_ms:
        with suppress(PWTimeout):
            page.wait_for_load_state("networkidle", timeout=remaining_ms)
    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
    if remaining_ms:
        page.wait_for_timeout(min(settle_ms, remaining_ms))


def _course_links(page: Page, console: Console) -> list[tuple[str, str]]:
    """从「My Courses」取课程链接：优先 My Courses 标签页，再退回首标签页。"""
    for url in (_PORTAL_COURSES, _PORTAL_HOME):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _wait_for_content(page, "a[href*='launcher?type=Course']", 3500)
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
    _wait_for_content(page, "a[href*='listContent.jsp']", 2500)
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
        _wait_for_content(page, "a[href*='bbcswebdav']", 1000)
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
    _wait_for_content(page, "#pastDueView li, #dueView li", 1200)
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


# ---------------------------------------------------------------- 公告测验解析

#: 月份前三字母 → 月号；容忍 Octorber 这类拼写错误与 Sept 这类缩写。
_MONTH_BY_PREFIX = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

#: 英文日期两种语序；分隔允许空格或点（Oct.13 / Oct 13 / 13 November 2026）。
_MONTH_DAY_RE = re.compile(
    r"\b(?P<month>[A-Za-z]{3,12})\.?[ \t]*(?P<day>\d{1,2})(?:st|nd|rd|th)?(?!\d)"
    r"(?:\s*,?\s*(?P<year>\d{4})(?!\d))?",
    re.IGNORECASE,
)
_DAY_MONTH_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?(?!\d)\s+(?P<month>[A-Za-z]{3,12})\.?"
    r"(?:\s*,?\s*(?P<year>\d{4})(?!\d))?",
    re.IGNORECASE,
)
_CN_DATE_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*年\s*)?(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"
)

#: 公告里的课堂测验关键词；\b 防止把 coding_quiz-1.zip 这类内部下划线文件名当测验。
_QUIZ_RE = re.compile(r"\bquiz\w*|小测|随堂测|测验", re.IGNORECASE)

#: 关键词之后最多检查这么多字符，避免把远处的日期牵强算成测验日期。
_QUIZ_DATE_WINDOW = 240


def _month_number(word: str) -> int | None:
    """按前三字母识别月份，返回月号；不是月份返回 None。"""
    return _MONTH_BY_PREFIX.get(word[:3].lower())


def _make_date(year: int | None, month: int, day: int, reference: date) -> date | None:
    """组装并校验日期，无效返回 None。

    缺年份时用 reference 的年份；若推断结果早于 reference，且处于「12 月公告提到
    次年 1/2 月」这类学期跨年场景，则顺延一年。显式写了年份的日期不做推断。
    """
    if year is None:
        try:
            value = date(reference.year, month, day)
        except ValueError:
            return None
        if value < reference and reference.month >= 9 and value.month <= 2:
            try:
                value = date(reference.year + 1, month, day)
            except ValueError:
                return None
        return value
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _find_dates(text: str, reference: date) -> list[tuple[int, date]]:
    """提取文本里的日期，返回按出现位置排序的 ``(位置, 日期)``；无效日期忽略。"""
    found: dict[int, date] = {}

    def add(pos: int, value: date | None) -> None:
        if value is not None and pos not in found:
            found[pos] = value

    for match in _DUE_DATE_RE.finditer(text):
        add(match.start(), parse_due_date(match.group(1)))
    for match in _CN_DATE_RE.finditer(text):
        raw_year = match.group("year")
        add(
            match.start(),
            _make_date(
                int(raw_year) if raw_year else None,
                int(match.group("month")),
                int(match.group("day")),
                reference,
            ),
        )
    for pattern in (_MONTH_DAY_RE, _DAY_MONTH_RE):
        for match in pattern.finditer(text):
            month = _month_number(match.group("month"))
            if month is None:
                continue
            raw_year = match.group("year")
            add(
                match.start(),
                _make_date(
                    int(raw_year) if raw_year else None,
                    month,
                    int(match.group("day")),
                    reference,
                ),
            )
    return sorted(found.items())


def parse_announcement_date(text: str, *, reference: date | None = None) -> date | None:
    """解析公告文本里的第一个日期；缺年份时用 reference（默认今天）补全。"""
    if not text:
        return None
    dates = _find_dates(text, reference or date.today())
    return dates[0][1] if dates else None


def _pick_future_date(candidates: list[tuple[int, date]], posted: date | None) -> date | None:
    """取第一个不早于公告发布时间的日期；全部早于发布时间视为历史信息。"""
    if posted is None:
        return candidates[0][1] if candidates else None
    for _, value in candidates:
        if value >= posted:
            return value
    return None


def _extract_quiz_date(title: str, body: str, posted: date | None) -> date | None:
    """从公告里定位测验日期。

    只看标题与正文中 quiz 关键词之后、同一段内的日期：日期必须写在测验
    名称后面，避免把关键词之前的作业截止日期误当成测验日期。
    """
    reference = posted or date.today()
    if _QUIZ_RE.search(title):
        chosen = _pick_future_date(_find_dates(title, reference), posted)
        if chosen:
            return chosen
    text = f"{title}\n{body}" if body else title
    for match in _QUIZ_RE.finditer(text):
        window = text[match.end() : match.end() + _QUIZ_DATE_WINDOW]
        para_end = window.find("\n\n")
        if para_end >= 0:
            window = window[:para_end]
        chosen = _pick_future_date(_find_dates(window, reference), posted)
        if chosen:
            return chosen
    return None


def _quiz_label_from_body(body: str) -> str:
    """标题为空时，用正文里带 quiz 关键词的那一行兜底做条目标题。"""
    for line in body.splitlines():
        if _QUIZ_RE.search(line):
            return line.strip()[:120]
    return ""


def parse_announcement_quiz_items(entries: list[dict[str, str]], course: Course) -> list[DueItem]:
    """把公告里带日期的课堂测验整理成待办条目。

    读不出日期、或日期早于公告发布时间的公告一律跳过：宁可不收录，
    也不要把「复习资料里提到 quiz」误当成待办。
    """
    items: list[DueItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        title = _clean_announcement_text(str(entry.get("title") or "")).replace("\n", " ").strip()
        body = _clean_announcement_text(str(entry.get("body_markdown") or entry.get("body") or ""))
        if not title and not body:
            continue
        posted = parse_announcement_date(str(entry.get("posted_on") or ""))
        due_date = _extract_quiz_date(title, body, posted)
        if due_date is None:
            continue
        label = title or _quiz_label_from_body(body) or course.code or course.title
        key = (due_date.isoformat(), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            DueItem(
                course_id=course.bb_id,
                course_code=course.code,
                course_title=course.title,
                title=label,
                due_date=due_date,
                kind="quiz",
            )
        )
    return items


def _clean_announcement_text(value: str) -> str:
    """归一文本：压缩行尾空白，但保留段落和列表产生的换行。"""
    text = value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def build_announcements_markdown(course_title: str, entries: list[dict[str, str]]) -> str:
    """把结构化公告条目渲染为 ``announcements.md`` 内容。"""
    lines = [f"# {course_title} — 公告", ""]
    for entry in entries:
        title = _clean_announcement_text(entry.get("title", "")).replace("\n", " ") or "(无标题)"
        posted_on = _clean_announcement_text(entry.get("posted_on", "")).replace("\n", " ")
        body = _clean_announcement_text(entry.get("body_markdown") or entry.get("body", ""))
        posted_by = _clean_announcement_text(entry.get("posted_by", ""))

        lines.extend([f"## {title}", ""])
        if posted_on:
            lines.extend([f"> {posted_on}", ""])
        if body:
            lines.extend([body, ""])
        if posted_by:
            lines.extend([f"> {posted_by.replace(chr(10), chr(10) + '> ')}", ""])
    return "\n".join(lines).rstrip() + "\n"


def scrape_announcement_entries(page: Page, course: Course) -> list[dict[str, str]]:
    """抓取课程公告页的结构化条目（不落盘；失败抛异常，由调用方决定处理方式）。"""
    page.goto(
        f"{BASE}/webapps/blackboard/execute/announcement?method=search"
        f"&context=mybb&viewChoice=2&course_id={course.bb_id}",
        wait_until="domcontentloaded",
        timeout=20000,
    )
    _wait_for_content(page, "#announcementList > li", 1000)
    # 经典版公告页的 li 本身没有 announcement class；唯一可靠的容器是列表本身。
    entries: list[dict[str, str]] = page.eval_on_selector_all(
        "#announcementList > li",
        """els => {
                const toMarkdown = root => {
                    const render = node => {
                        if (!node) return '';
                        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || '';
                        if (node.nodeType !== Node.ELEMENT_NODE) return '';
                        const tag = node.tagName.toLowerCase();
                        const content = Array.from(node.childNodes).map(render).join('');
                        if (tag === 'br') return '\\n';
                        if (tag === 'p') return content.trim() + '\\n\\n';
                        if (tag === 'a') {
                            const label = content.trim();
                            return node.href
                                ? '[' + (label || node.href) + '](' + node.href + ')'
                                : label;
                        }
                        if (tag === 'img') {
                            const alt = (node.getAttribute('alt') || '公告图片').trim();
                            return node.src ? '![' + alt + '](' + node.src + ')' : '';
                        }
                        if (tag === 'strong' || tag === 'b') return '**' + content.trim() + '**';
                        if (tag === 'em' || tag === 'i') return '*' + content.trim() + '*';
                        if (tag === 'code') return '`' + content.trim() + '`';
                        if (tag === 'ul' || tag === 'ol') {
                            const ordered = tag === 'ol';
                            const start = Number.parseInt(node.getAttribute('start') || '1', 10) || 1;
                            let index = 0;
                            return Array.from(node.children)
                                .filter(child => child.tagName && child.tagName.toLowerCase() === 'li')
                                .map(li => {
                                    const prefix = ordered ? (start + index++) + '. ' : '- ';
                                    const item = render(li).trim().replace(
                                        /\\n/g, '\\n' + ' '.repeat(prefix.length)
                                    );
                                    return prefix + item + '\\n';
                                }).join('') + '\\n';
                        }
                        if (tag === 'li') return content.trim() + '\\n';
                        if (tag === 'hr') return '\\n---\\n\\n';
                        return content;
                    };
                    return render(root)
                        .replace(/[ \\t]+\\n/g, '\\n')
                        .replace(/\\n{3,}/g, '\\n\\n')
                        .trim();
                };
                return els.map(li => {
                    const details = li.querySelector('.details');
                    const body = details?.querySelector('.vtbegenerated');
                    const info = li.querySelector('.announcementInfo');
                    return {
                        title: (li.querySelector('h3.item, h3')?.innerText || '').trim(),
                        posted_on: (details?.querySelector('p')?.innerText || '').trim(),
                        body_markdown: body ? toMarkdown(body) : '',
                        posted_by: Array.from(info?.querySelectorAll('p') || [])
                            .map(p => (p.innerText || '').trim()).filter(Boolean).join('\\n'),
                    };
                });
            }""",
    )
    return entries


def scrape_announcements(
    page: Page, course: Course, course_dir: Path, dry_run: bool, console: Console
) -> int:
    """抓取公告写入 ``announcements.md``；返回写入条数（失败不阻塞主流程）。"""
    try:
        entries = scrape_announcement_entries(page, course)
        count = len(entries)
        if count and not dry_run:
            course_dir.mkdir(parents=True, exist_ok=True)
            (course_dir / "announcements.md").write_text(
                build_announcements_markdown(course.title, entries), encoding="utf-8"
            )
            console.log(f"    公告 x{count} → announcements.md")
        return count
    except Exception as exc:  # 公告失败不阻塞主流程
        console.warn(f"公告抓取失败: {exc}")
        return 0


__all__ = [
    "categorize",
    "build_announcements_markdown",
    "collect_menu_links",
    "find_files",
    "make_slug",
    "match_category",
    "parse_announcement_date",
    "parse_announcement_quiz_items",
    "parse_due_date",
    "parse_due_entries",
    "sanitize_filename",
    "scrape_announcement_entries",
    "scrape_announcements",
    "scrape_courses",
    "scrape_due_items",
]
