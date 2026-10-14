"""彩色 help：给 ``--help`` / ``-h`` 与无参数帮助加上 ANSI 样式。

**为什么不用 rich**：rich 的导入约 45 ms，而 ``bb-sync --help`` 整条路径目前约
120 ms。为了给帮助上色把冷启动拖慢三分之一，与 :class:`~bb_sync.cli.LazyGroup`
的优化目标直接冲突。这里只用 ``click.style`` 拼转义序列，导入成本为零。

**为什么不用自己判断 TTY**：Click ≥8.4 的 help 排版按「剥离 ANSI 后的可见宽度」
计算（``term_len`` 内部走 ``strip_ansi``，``wrap_text`` 亦已 ANSI-aware），
所以往 formatter 缓冲里插转义序列不会破坏列对齐；而 ``echo`` 在输出不像交互式
终端时会统一剥掉 ANSI。于是「染」与「不染」都由 Click 自己决定，管道、重定向、
``CliRunner`` 场景天然退回纯文本，无需额外分支。

**文案**：Click 自带的 ``-h, --help`` 与 ``--version`` 说明是硬编码英文，
与项目其余中文说明不一致，这里连同样式一起统一。

**为什么用 mixin 而不是固定的 Context 子类**：Typer 0.27 vendored 了一份自己的
``_click``，``typer._click.core.Context`` 与官方 ``click.Context`` 是两套互不继承
的类。如果给 Typer 命令硬塞官方 ``click.Context`` 子类，上下文会被压进官方 Click
的栈，而 Typer 侧查的是自己的栈，命令里立刻报
``RuntimeError: There is no active click context``。所以这里只做 mixin，
在每个命令**原有**的 Context 基类上派生一个子类，只换 ``formatter_class``。
"""

from __future__ import annotations

from collections.abc import Iterable

import click

#: 选项名 / 命令名的样式；描述文字保持默认色——给第二列上色收益有限，
#: 却要额外承担 ``\b`` 不重排段落与换行边界的交互风险。
_TERM_COLOR = "cyan"

#: Click 硬编码的 help 选项文案是英文，换成中文以与其余说明一致
_HELP_OPTION_TEXT = "显示帮助并退出"


class PrettyHelpFormatter(click.HelpFormatter):
    """带 ANSI 样式的 help formatter。

    只染三类位置：章节标题、``Usage:`` 前缀、定义列表第一列（选项名 / 命令名）。
    这三处要么不参与折行，要么已被 ANSI-aware 的宽度计算覆盖。
    """

    def write_heading(self, heading: str) -> None:
        # 冒号由父类补，故意不一起加粗
        super().write_heading(click.style(heading, bold=True))

    def write_usage(self, prog: str, args: str = "", prefix: str | None = None) -> None:
        # 父类用 ``f"{prefix:>{indent}}{prog} "`` 拼前缀、用 ``term_len(prefix)``
        # 算悬挂缩进——term_len 会剥掉 ANSI，所以染 prefix 不影响换行对齐。
        label = (prefix if prefix is not None else "Usage: ").rstrip()
        super().write_usage(prog, args, click.style(label, bold=True) + " ")

    def write_dl(
        self,
        rows: Iterable[tuple[str, str]],
        col_max: int = 30,
        col_spacing: int = 2,
    ) -> None:
        # 列宽由 measure_table / term_len 决定，两者都按可见宽度算，
        # 所以给第一列上色不会让后续列错位。
        styled = [
            (click.style(first, fg=_TERM_COLOR) if first else first, second)
            for first, second in rows
        ]
        super().write_dl(styled, col_max, col_spacing)


class _PrettyMixin:
    """把 :class:`PrettyHelpFormatter` 装进任意 Context 基类的标记 mixin。"""

    formatter_class = PrettyHelpFormatter


def _install_pretty_context(command: click.Command) -> None:
    """在命令原有的 Context 基类上派生一个带样式的子类。

    派生而非替换基类，是为了不打断 Typer 自己那份 vendored click 的上下文栈
    （见模块 docstring）。同一个基类只派生一次，靠 ``issubclass`` 判断。
    """
    base = command.context_class
    if issubclass(base, _PrettyMixin):
        return
    command.context_class = type(
        f"Pretty{base.__name__}",
        (_PrettyMixin, base),
        {"__module__": __name__},
    )


def _localize_help_option(command: click.Command) -> None:
    """把 Click 硬编码的英文 help 选项文案换成中文。

    Click 8.1.8 起 ``get_help_option`` 会把选项对象缓存在 ``_help_option`` 里，
    所以包一层、在返回前改 ``help`` 即可，不必去碰私有属性。存成实例属性后
    不再经过描述符绑定，``localized`` 因此只收 ``ctx`` 一个参数。
    """
    original = command.get_help_option

    def localized(ctx: click.Context) -> click.Option | None:
        option = original(ctx)
        if option is not None:
            option.help = _HELP_OPTION_TEXT
        return option

    command.get_help_option = localized  # type: ignore[method-assign]


def apply_help_style(command: click.Command) -> None:
    """递归给 ``command`` 及其全部子命令应用统一的 help 呈现。

    两件事：彩色 formatter（:func:`_install_pretty_context`）与中文 help 选项
    文案（:func:`_localize_help_option`）。Click 的 ``context_class`` 与
    ``get_help_option`` 都不会自动传给子命令，所以必须递归。
    """
    _install_pretty_context(command)
    _localize_help_option(command)
    for sub in getattr(command, "commands", {}).values():
        apply_help_style(sub)


__all__ = ["PrettyHelpFormatter", "apply_help_style"]
