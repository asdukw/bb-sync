"""路径解析：兼容「源码运行」与「安装为 CLI」两种形态。

用户文件（config.yaml）查找顺序：当前工作目录优先（项目内用法），
其次 ~/.bb-sync/（安装后的用户配置目录）。浏览器 profile、日志、调试产物
放 ~/.bb-sync/，可用环境变量 BB_SYNC_HOME 覆盖；Steel 后端固定安装在
~/.steel/，不跟随 BB_SYNC_HOME，避免不同环境产生多份 .steel。
"""

from __future__ import annotations

import os
from pathlib import Path

BB_SYNC_HOME = Path(os.environ.get("BB_SYNC_HOME") or Path.home() / ".bb-sync")

CONFIG_NAME = "config.yaml"


def find_config_file(name: str = CONFIG_NAME) -> Path:
    """查找用户配置文件：当前工作目录优先，其次 ~/.bb-sync/。"""
    cwd_candidate = Path.cwd() / name
    if cwd_candidate.exists():
        return cwd_candidate
    return BB_SYNC_HOME / name


__all__ = ["BB_SYNC_HOME", "CONFIG_NAME", "find_config_file"]
