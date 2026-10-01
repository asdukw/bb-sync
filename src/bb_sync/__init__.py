"""bb-sync — Blackboard 课程资源自动同步。

对外暴露版本号与核心异常，具体功能走各子包：

- :mod:`bb_sync.cli`            Typer 命令行入口
- :mod:`bb_sync.core`           配置、输出、异常等基础设施
- :mod:`bb_sync.blackboard`     Blackboard 站点交互（登录 / 抓取 / 下载）
- :mod:`bb_sync.browser`        Steel 浏览器后端
"""

from __future__ import annotations

import importlib.metadata as metadata

try:
    __version__ = metadata.version("bb-sync")
except metadata.PackageNotFoundError:  # 源码直跑、未安装时
    __version__ = "0.0.0.dev0"

#: 问题反馈入口；错误提示统一引用，避免多处硬编码
ISSUES_URL = "https://github.com/asdukw/bb-sync/issues"

__all__ = ["ISSUES_URL", "__version__"]
