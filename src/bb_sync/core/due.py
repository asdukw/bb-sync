"""把课程 Due / To Do 数据渲染为按优先级排列的 ``due.md``。"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from bb_sync.blackboard.models import BASE, Course, DueItem

#: 分组顺序就是学生处理任务的优先级。
_BUCKET_ORDER = ("overdue", "today", "tomorrow", "next_7_days", "future", "unknown")
_BUCKET_LABELS = {
    "overdue": "已逾期",
    "today": "今天",
    "tomorrow": "明天",
    "next_7_days": "未来 7 天",
    "future": "之后",
    "unknown": "日期未知",
}

#: 非普通任务的待办类型标签（来自公告的课堂测验等）。
_KIND_LABELS = {"quiz": "测验"}


def kind_label(kind: str) -> str:
    """返回待办类型的中文标签；普通任务返回空串。"""
    return _KIND_LABELS.get(kind, "")


def _bucket(item: DueItem, today: date) -> str:
    """按剩余时间归类；无法解析日期的任务放最后，避免被误判成低优先级。"""
    if item.due_date is None:
        return "unknown"
    if item.due_date < today:
        return "overdue"
    if item.due_date == today:
        return "today"
    if item.due_date == today + timedelta(days=1):
        return "tomorrow"
    if item.due_date <= today + timedelta(days=7):
        return "next_7_days"
    return "future"


def _relative_due(item: DueItem, today: date) -> str:
    """生成截止日期旁的中文提示。"""
    if item.due_date is None:
        return item.due_text or "日期未知"
    delta = (item.due_date - today).days
    if delta < 0:
        return f"逾期 {-delta} 天"
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    return f"还有 {delta} 天"


def _escape_markdown(text: str) -> str:
    """转义会改变 Markdown 结构的常见字符，同时保留可读标题。"""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def _sort_key(item: DueItem) -> tuple[date, str, str]:
    return (item.due_date or date.max, item.course_label, item.title)


def sort_due_items(items: list[DueItem]) -> list[DueItem]:
    """按截止日期、课程和任务名返回稳定排序的新列表。"""
    return sorted(items, key=_sort_key)


def build_due_markdown(
    courses: list[Course],
    items: list[DueItem],
    *,
    failed: int = 0,
    today: date | None = None,
    generated_at: datetime | None = None,
) -> str:
    """生成全局 ``due.md`` 文本。

    输出按「已逾期 → 今天 → 明天 → 未来 7 天 → 之后 → 日期未知」排序；
    同一分组内再按日期、课程和任务名稳定排序。
    """
    day = today or date.today()
    generated = generated_at or datetime.now().astimezone()
    grouped: dict[str, list[DueItem]] = defaultdict(list)
    for item in items:
        grouped[_bucket(item, day)].append(item)
    for bucket_items in grouped.values():
        bucket_items.sort(key=_sort_key)

    quiz_count = sum(1 for item in items if item.kind == "quiz")
    lines = [
        "# Blackboard 待办",
        "",
        f"> 更新时间：{generated.strftime('%Y-%m-%d %H:%M')}（本地时间）",
        f"> 已检查 {len(courses)} 门课程，发现 {len(items)} 项待办；按优先级和截止日期排列。",
    ]
    if quiz_count:
        lines.append(f"> 其中 {quiz_count} 项是公告里的课堂测验，已一并作为待办列出。")
    if failed:
        lines.append(f"> 注意：{failed} 门课程抓取失败，结果可能不完整。")
    lines.append("")

    if not items:
        if failed:
            lines.extend(["本次未读取到待办，但由于部分课程抓取失败，结果可能不完整。", ""])
        else:
            lines.extend(["## Congratulations! 🎉", "", "当前没有待办事项。", ""])
        return "\n".join(lines)

    for bucket in _BUCKET_ORDER:
        bucket_items = grouped.get(bucket, [])
        if not bucket_items:
            continue
        lines.append(f"## {_BUCKET_LABELS[bucket]}（{len(bucket_items)}）")
        lines.append("")
        for item in bucket_items:
            due = item.due_date.isoformat() if item.due_date else "日期未知"
            course_url = (
                f"{BASE}/webapps/blackboard/execute/launcher?type=Course&id={item.course_id}"
            )
            label = kind_label(item.kind)
            badge = f"【{label}】" if label else ""
            lines.append(
                f"- **{due}**（{_relative_due(item, day)}） — "
                f"[{_escape_markdown(item.course_label)}]({course_url}) — "
                f"{badge}{_escape_markdown(item.title)}"
            )
        lines.append("")
    return "\n".join(lines)


def write_due_markdown(
    path: Path,
    courses: list[Course],
    items: list[DueItem],
    *,
    failed: int = 0,
    today: date | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """写入 ``due.md``，父目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_due_markdown(
            courses,
            items,
            failed=failed,
            today=today,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    return path


__all__ = ["build_due_markdown", "kind_label", "sort_due_items", "write_due_markdown"]
