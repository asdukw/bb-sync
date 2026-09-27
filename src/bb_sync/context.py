"""全局运行上下文：把 Typer 的全局选项传给各子命令。

Typer 的子命令之间不共享状态，所以全局开关（``--json`` / ``--quiet`` / ``--verbose``
/ ``--config``）统一放在顶层回调里，存进 :class:`Context` 对象，由命令层按需取用。

性能要点：本模块**不在顶层导入** pydantic（``core.config``）与 rich
(``core.output``)，否则 ``bb-sync --version`` 也要为它们付出百毫秒级导入成本。
两者都在真正被用到时才导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bb_sync import paths

if TYPE_CHECKING:  # 仅供类型检查，运行时不导入
    from bb_sync.core.config import Settings
    from bb_sync.core.output import Console

#: 模块级缓存：Console 类在首次使用时才导入
_CONSOLE_CLS: Any = None


def _console_cls() -> Any:
    global _CONSOLE_CLS
    if _CONSOLE_CLS is None:
        from bb_sync.core.output import Console

        _CONSOLE_CLS = Console
    return _CONSOLE_CLS


@dataclass
class Context:
    """一次 CLI 调用的共享状态。"""

    json_mode: bool = False
    quiet: bool = False
    verbose: bool = False
    config_file: str | None = None
    _console: Any = field(default=None, repr=False)

    @property
    def console(self) -> Console:
        """惰性构造 Console（首次访问时才导入 rich）。"""
        if self._console is None:
            self._console = _console_cls()(
                json_mode=self.json_mode, quiet=self.quiet, verbose=self.verbose
            )
        return self._console

    @property
    def home(self) -> Path:
        return paths.BB_SYNC_HOME

    def load_settings(self, *, cli_overrides: dict | None = None) -> tuple[Settings, Path]:
        """按四层优先级加载配置（首次调用时才导入 pydantic）。"""
        from bb_sync.core.config import load_settings

        return load_settings(
            Path(self.config_file).expanduser() if self.config_file else None,
            cli_overrides=cli_overrides,
        )


__all__ = ["Context"]
