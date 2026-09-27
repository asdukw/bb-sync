"""Blackboard 领域模型与站点常量。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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


__all__ = [
    "ADFS_NEXT",
    "ADFS_PASS",
    "ADFS_SUBMIT",
    "ADFS_USER",
    "BASE",
    "LOGIN_URL",
    "Course",
    "FileItem",
    "SyncStats",
]
