"""同步服务：编排「登录 → 列课 → 抓内容 → 下载」的完整流程。

这一层是 CLI 与站点抓取之间的胶水，负责：
- 把 :class:`~bb_sync.core.config.Settings` 转成具体执行参数
- 驱动 Steel 会话的生命周期（起服务、建会话、接入 CDP、释放）
- 逐课程推进并把结果汇总成 :class:`SyncStats`

本层不直接 ``print``：全部通过注入的 :class:`~bb_sync.core.output.Console`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from bb_sync.blackboard import downloader, scraper
from bb_sync.blackboard.login import ensure_login
from bb_sync.blackboard.models import Course, DueItem, DueStats, FileItem, SyncStats
from bb_sync.browser import steel
from bb_sync.core import due
from bb_sync.core.config import Settings
from bb_sync.core.errors import ConfigError, NetworkError, NotFoundError
from bb_sync.core.output import Console


@dataclass
class SyncOptions:
    """一次同步的运行时选项（已完成配置分层合并）。"""

    settings: Settings
    config_path: Path
    root: Path
    console: Console
    headed: bool = False
    dry_run: bool = False
    courses: list[str] | None = None


@dataclass
class DueOptions:
    """``bb-sync due`` 的运行时选项。"""

    settings: Settings
    root: Path
    console: Console
    headed: bool = False
    courses: list[str] | None = None


def plan_root(settings: Settings, config_path: Path, cli_root: str | None) -> Path:
    """计算下载根目录：CLI ``--root`` > 配置 ``root``。

    相对路径基准：CLI 传的相对路径以 CWD 为基准，配置里的相对路径以配置文件目录为基准。
    """
    if cli_root:
        raw = Path(cli_root).expanduser()
        return raw if raw.is_absolute() else (Path.cwd() / raw).resolve()
    return settings.resolve_root(config_path)


def _drive_available(root: Path) -> bool:
    """检查带盘符/网络共享的绝对路径，其根位置当前是否存在。"""
    if not root.drive:
        return True
    try:
        return Path(root.anchor).exists()
    except OSError:
        return False


def ensure_root_directory(root: Path) -> bool:
    """确保下载根目录可用，返回调用前该目录是否尚不存在。"""
    first_run = not root.exists()
    if not _drive_available(root):
        raise ConfigError(
            f"下载根目录所在磁盘不可用：{root.drive}（当前配置：{root}）",
            "请确认该磁盘已连接/挂载，或用 --root <可用目录> 临时指定；"
            "也可运行 bb-sync config set root <可用目录> 永久修改",
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"无法创建下载根目录：{root}",
            f"{exc}；请检查路径权限，或运行 bb-sync config set root <可用目录> 修改配置",
        ) from exc
    return first_run


def folder_name(course: Course, explicit_dirs: dict[str, str]) -> str:
    """课程本地文件夹名：显式映射优先，否则自动生成。"""
    if course.code and course.code in explicit_dirs:
        return explicit_dirs[course.code]
    return scraper.make_slug(course)


def sync_course(
    ctx,
    page,
    course: Course,
    course_dir: Path,
    options: SyncOptions,
    stats: SyncStats,
) -> None:
    """同步单门课程：公告 → 内容区 → 逐文件下载。"""
    console = options.console
    settings = options.settings
    keywords = settings.keywords

    console.step(f"=== {course.code or '?'} | {course.title} → {course_dir} ===")
    if not options.dry_run:
        course_dir.mkdir(parents=True, exist_ok=True)

    if settings.announcements:
        scraper.scrape_announcements(page, course, course_dir, options.dry_run, console)

    all_items: list[FileItem] = []
    visited: set[str] = set()
    menu = scraper.collect_menu_links(page, course)
    console.log("    内容区: " + (", ".join(n for _, n in menu) or "(无)"))
    for area_url, area_name in menu:
        all_items += scraper.find_files(
            page, area_url, area_name, 1, settings.max_depth, keywords, visited
        )

    # 去重（同一文件可能出现在多个位置）
    uniq: dict[str, FileItem] = {}
    for item in all_items:
        uniq.setdefault(item.url, item)
    console.log(f"    发现 {len(uniq)} 个文件")

    index = downloader.build_index(course_dir)
    for item in uniq.values():
        try:
            status, name = downloader.download_item(
                ctx.request, item, course_dir, options.dry_run, index
            )
        except Exception as exc:  # 单个文件失败不中断整门课
            stats.bump("failed")
            console.warn(f"    [{item.category}] 下载失败: {exc}")
            continue
        stats.bump(status)
        _log_item(console, status, item.category, name)


def _log_item(console: Console, status: str, category: str, name: str) -> None:
    """按状态打印单条下载结果（全部走 stderr）。"""
    if status == "downloaded":
        console.log(f"    ↓ [{category}] {name}")
    elif status == "would-download":
        console.log(f"    ? [{category}] {name} (dry-run)")
    elif status == "exists":
        console.log(f"    = [{category}] {name} (已存在，跳过)")
    elif status == "empty":
        console.warn(f"    [{category}] {name} 内容为空，跳过")


def _print_plan(console: Console, courses: list[Course], root: Path, dry_run: bool) -> None:
    """开始下载前，先打印「将要写入哪些目录」的计划，方便确认落点。

    每个课程目录都显示为**绝对路径**——相对路径容易让人误判落盘位置。
    """
    console.rule("同步计划")
    console.log(f"根目录: {root}")
    console.log("课程目录:")
    for course in courses:
        label = course.code or course.title
        console.log(f"  {label:<10} → {root / course.slug}")
    console.log(f"合计 {len(courses)} 门课程" + ("（dry-run，不会写入）" if dry_run else ""))
    console.log("")


def _scan_due_items(page, courses: list[Course], console: Console) -> tuple[list[DueItem], int]:
    """逐课程抓取 Due，并返回排序后的条目与失败课程数。"""
    items: list[DueItem] = []
    failed = 0
    for course in courses:
        try:
            found = scraper.scrape_due_items(page, course, console)
        except Exception as exc:  # 单门课失败不阻断其它课程
            failed += 1
            console.warn(f"[due] {course.code or course.title} 抓取失败: {exc}")
            continue
        items.extend(found)
        console.log(f"    {course.code or course.title}: 待办 x{len(found)}")
    return due.sort_due_items(items), failed


def run_sync(options: SyncOptions) -> SyncStats:
    """执行一次完整同步。"""
    console = options.console
    settings = options.settings

    first_run = ensure_root_directory(options.root)
    if first_run:
        console.log(f"[info] 首次运行：课程文件将下载到 {options.root}")
        console.log("       （永久修改：bb-sync config set root <目录>；临时指定：--root）")

    include = options.courses if options.courses else settings.include
    explicit_dirs = settings.course_dirs
    stats = SyncStats()

    # ---- Steel 后端：独立进程、静默无窗口、登录态持久化在 profile ----
    steel.ensure_server(console, options.headed)
    session = steel.create_session(options.headed, console)

    try:
        with sync_playwright() as p:
            browser, ctx, page = steel.connect(p, session)
            console.debug(f"[steel] CDP 已接入 (UA: {ctx.browser.version if ctx.browser else '?'})")

            ensure_login(page, console, headed=options.headed)

            courses = scraper.scrape_courses(page, include, console)
            if not courses:
                raise NotFoundError(
                    "未发现课程",
                    "确认已登录且账号下有课程；也可用 bb-sync course list 先看看",
                )

            # 先算出每门课的落盘目录并整体打印，再逐门开始下载
            for course in courses:
                course.slug = folder_name(course, explicit_dirs)
            _print_plan(console, courses, options.root, options.dry_run)

            for course in courses:
                stats.courses.append(course.code or course.title)
                sync_course(ctx, page, course, options.root / course.slug, options, stats)

            due_items, due_failed = _scan_due_items(page, courses, console)
            due_path = options.root / "due.md"
            if options.dry_run:
                if due_failed == len(courses):
                    message = "待办抓取全部失败"
                elif not due_items and not due_failed:
                    message = "Congratulations! 没有待办"
                else:
                    message = f"待办 {len(due_items)} 项"
                console.log(f"[due] {message}（dry-run，未写入 due.md）")
            elif due_failed == len(courses):
                console.warn("[due] 所有课程待办抓取失败，保留现有 due.md")
            else:
                due.write_due_markdown(due_path, courses, due_items, failed=due_failed)
                if due_items:
                    console.log(f"[due] {len(due_items)} 项 → due.md")
                elif due_failed:
                    console.log("[due] 未读取到待办（部分课程失败）→ due.md")
                else:
                    console.log("[due] Congratulations! 没有待办 → due.md")

            ctx.close()
            browser.close()
    finally:
        steel.release_session(session, console)

    _print_summary(console, stats, options.dry_run)
    return stats


def _print_summary(console: Console, stats: SyncStats, dry_run: bool) -> None:
    """汇总输出（stderr，人读）。"""
    console.rule("汇总")
    if dry_run:
        console.log(f"待下载: {stats.would_download} 个文件（未实际下载）")
    else:
        console.log(f"新下载 {stats.downloaded} 个，已存在跳过 {stats.exists} 个")
    if stats.failed:
        console.warn(f"失败 {stats.failed} 个")


def list_courses(options: SyncOptions) -> list[Course]:
    """仅列出课程（不下载），供 ``bb-sync course list`` 使用。"""
    console = options.console
    steel.ensure_server(console, options.headed)
    session = steel.create_session(options.headed, console)
    try:
        with sync_playwright() as p:
            browser, ctx, page = steel.connect(p, session)
            ensure_login(page, console, headed=options.headed)
            include = options.courses if options.courses else "all"
            courses = scraper.scrape_courses(page, include, console)
            for course in courses:
                course.slug = folder_name(course, options.settings.course_dirs)
            ctx.close()
            browser.close()
    finally:
        steel.release_session(session, console)
    return courses


def run_due(options: DueOptions) -> DueStats:
    """扫描课程主页的 To Do 模块，并生成全局 ``due.md``。"""
    console = options.console
    settings = options.settings
    first_run = ensure_root_directory(options.root)
    if first_run:
        console.log(f"[info] 待办文件将写入 {options.root / 'due.md'}")

    include = options.courses if options.courses else settings.include
    stats = DueStats(path=str(options.root / "due.md"))

    steel.ensure_server(console, options.headed)
    session = steel.create_session(options.headed, console)
    try:
        with sync_playwright() as p:
            browser, ctx, page = steel.connect(p, session)
            ensure_login(page, console, headed=options.headed)

            courses = scraper.scrape_courses(page, include, console)
            if not courses:
                raise NotFoundError(
                    "未发现课程",
                    "确认已登录且账号下有课程；也可用 bb-sync course list 先看看",
                )

            stats.courses = [course.code or course.title for course in courses]
            items, stats.failed = _scan_due_items(page, courses, console)

            if stats.failed == len(courses):
                raise NetworkError(
                    "所有课程主页都抓取失败",
                    "检查网络连接与登录状态，或先用 bb-sync run --headed 刷新登录态",
                )

            stats.items = items
            due.write_due_markdown(
                options.root / "due.md",
                courses,
                items,
                failed=stats.failed,
            )
            ctx.close()
            browser.close()
    finally:
        steel.release_session(session, console)

    console.rule("待办汇总")
    console.log(f"发现 {stats.total} 项待办，已写入 {stats.path}")
    if stats.failed:
        console.warn(f"有 {stats.failed} 门课程抓取失败，due.md 可能不完整")
    return stats


__all__ = [
    "SyncOptions",
    "DueOptions",
    "ensure_root_directory",
    "folder_name",
    "list_courses",
    "plan_root",
    "run_sync",
    "run_due",
    "sync_course",
]
