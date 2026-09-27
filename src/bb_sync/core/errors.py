"""异常体系与退出码。

约定：CLI 层只捕获 :class:`BbSyncError`，由它自带的 ``exit_code`` 决定进程退出码；
其他未预期异常统一归为 ``GENERAL_ERROR``，并打印堆栈方便报 bug。

退出码语义（对齐 BSD sysexits 精神，保持小整数便于脚本判断）::

    0  SUCCESS           成功
    1  GENERAL_ERROR     未分类错误
    2  USAGE_ERROR       命令行用法错误（Typer/Click 自身也用 2）
    3  AUTH_ERROR        登录 / 凭据失败
    4  CONFIG_ERROR      配置文件问题
    5  NETWORK_ERROR     网络 / 代理 / 下载失败
    6  NOT_FOUND         目标资源不存在
    7  ENVIRONMENT_ERROR 依赖缺失（Node / 浏览器 / Steel 等）
    8  INTERRUPTED       用户中断（Ctrl+C）
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """进程退出码。"""

    SUCCESS = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2
    AUTH_ERROR = 3
    CONFIG_ERROR = 4
    NETWORK_ERROR = 5
    NOT_FOUND = 6
    ENVIRONMENT_ERROR = 7
    INTERRUPTED = 8


class BbSyncError(Exception):
    """所有可预期错误的基类，自带退出码与可选修复提示。"""

    exit_code: ExitCode = ExitCode.GENERAL_ERROR

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(BbSyncError):
    """命令行参数用法错误。"""

    exit_code = ExitCode.USAGE_ERROR


class ConfigError(BbSyncError):
    """配置文件缺失、格式错误或不支持的操作。"""

    exit_code = ExitCode.CONFIG_ERROR


class AuthError(BbSyncError):
    """登录失败、凭据无效或缺失。"""

    exit_code = ExitCode.AUTH_ERROR


class NetworkError(BbSyncError):
    """网络不可达、代理异常、下载失败。"""

    exit_code = ExitCode.NETWORK_ERROR


class NotFoundError(BbSyncError):
    """课程 / 配置键 / 文件等目标不存在。"""

    exit_code = ExitCode.NOT_FOUND


class EnvironmentError_(BbSyncError):
    """运行环境不满足（Node / npm / 浏览器 / Steel 未就绪）。

    名字带下划线后缀，避免与内置 :class:`EnvironmentError` 混淆。
    """

    exit_code = ExitCode.ENVIRONMENT_ERROR


def exit_code_for(exc: BaseException) -> ExitCode:
    """把任意异常映射为退出码。"""
    if isinstance(exc, BbSyncError):
        return exc.exit_code
    if isinstance(exc, KeyboardInterrupt):
        return ExitCode.INTERRUPTED
    return ExitCode.GENERAL_ERROR


__all__ = [
    "AuthError",
    "BbSyncError",
    "ConfigError",
    "EnvironmentError_",
    "ExitCode",
    "NetworkError",
    "NotFoundError",
    "UsageError",
    "exit_code_for",
]
