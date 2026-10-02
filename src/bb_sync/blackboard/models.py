"""Blackboard 领域模型与站点常量。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

BASE = "https://bb.cuhk.edu.cn"

#: ADFS 页面元素选择器
ADFS_USER = "input[name='userNameInput'], #userNameInput"
ADFS_PASS = "input[name='PasswordInput'], #passwordInput"
ADFS_NEXT = "#nextButton, #submitButton"  # 第一页「下一步」
ADFS_SUBMIT = "#submitButton, #nextButton, span.submit"  # 密码页「登录」
LOGIN_URL = f"{BASE}/webapps/login/"

_PORTAL_HOME = f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"
_PORTAL_COURSES = f"{BASE}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_25_1"


@dataclass
class Course:
    """一门课程。"""

    bb_id: str  # e.g. _12345_1
    title: str  # 原始标题
    code: str = ""  # 提取的课程代码，如 CSC5010
    slug: str = ""  # 本地文件夹名

    def __post_init__(self) -> None:
        m = re.search(r"\b([A-Z]{3,4}\d{4}[A-Z]?)\b", self.title.upper())
        if m:
            self.code = m.group(1)

    def as_dict(self) -> dict[str, str]:
        return {"id": self.bb_id, "code": self.code, "title": self.title, "folder": self.slug}


@dataclass
class FileItem:
    """内容区里的一个待下载条目。"""

    name: str | None
    url: str
    category: str  # lectures / assignments / tutorials / other
    folder_hint: str = ""  # 内容区路径提示


@dataclass(frozen=True)
class DueItem:
    """一项待办：课程主页 Due / To Do 任务，或从公告整理出的课堂测验。"""

    course_id: str
    course_code: str
    course_title: str
    title: str
    due_date: date | None
    due_text: str = ""
    kind: str = "task"  # task = To Do 模块；quiz = 公告里的课堂测验

    @property
    def course_label(self) -> str:
        """优先显示课程代码，没有代码时退回原始标题。"""
        return self.course_code or self.course_title or self.course_id

    def as_dict(self) -> dict[str, str | None]:
        return {
            "course_id": self.course_id,
            "course_code": self.course_code,
            "course_title": self.course_title,
            "title": self.title,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "due_text": self.due_text,
            "kind": self.kind,
        }


@dataclass
class SyncStats:
    """一次同步的统计结果。"""

    downloaded: int = 0
    exists: int = 0
    would_download: int = 0
    empty: int = 0
    failed: int = 0
    courses: list[str] = field(default_factory=list)

    def bump(self, status: str) -> None:
        key = status.replace("-", "_")
        if hasattr(self, key):
            setattr(self, key, getattr(self, key) + 1)

    def as_dict(self) -> dict[str, object]:
        return {
            "downloaded": self.downloaded,
            "exists": self.exists,
            "would_download": self.would_download,
            "empty": self.empty,
            "failed": self.failed,
            "courses": self.courses,
        }


@dataclass
class DueStats:
    """一次 ``bb-sync due`` 的结果。"""

    path: str = ""
    items: list[DueItem] = field(default_factory=list)
    failed: int = 0  # Due / To Do 模块抓取失败的课程数
    announcement_failed: int = 0  # 公告抓取失败的课程数
    courses: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "total": self.total,
            "failed": self.failed,
            "announcement_failed": self.announcement_failed,
            "courses": self.courses,
            "items": [item.as_dict() for item in self.items],
        }


__all__ = [
    "ADFS_NEXT",
    "ADFS_PASS",
    "ADFS_SUBMIT",
    "ADFS_USER",
    "BASE",
    "LOGIN_URL",
    "Course",
    "DueItem",
    "DueStats",
    "FileItem",
    "SyncStats",
]
