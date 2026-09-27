"""bb-sync 命令行入口（Typer + Click 延迟注册）。

设计要点：

- **全局选项与子命令分离**：``--json/--quiet/--verbose/--config`` 是全局开关，
  子命令只管自己的业务参数
- **退出码语义化**：所有异常在 :func:`main` 统一兜底，由
  :class:`~bb_sync.core.errors.BbSyncError` 决定退出码，脚本可据此分支
- **stdout/stderr 严格分流**：结果走 stdout（``--json`` 时是纯 JSON），
  日志/进度/错误走 stderr
- **冷启动优先**：命令模块延迟导入（见 :class:`LazyGroup`），
  避免 ``--version`` / ``--help`` 被 playwright/pydantic/keyring 拖慢

用法速查::

    bb-sync run                    # 增量同步全部课程
    bb-sync run --course CSC5010   # 只同步一门课
    bb-sync course list --json     # 机器可读地列出课程
    bb-sync config set root D:/courses
    bb-sync doctor
    bb-sync auth login / logout / status
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import click

from bb_sync import __version__
from bb_sync.core.errors import BbSyncError, ExitCode, exit_code_for

#: 命令名 → (模块名, 一句话帮助)
SUBCOMMANDS: dict[str, tuple[str, str]] = {
    "run": ("run", "执行一次课程同步"),
    "doctor": ("doctor", "体检：检查前置条件（git / Node / 浏览器 / 网络等）"),
    "config": ("config", "查看 / 修改配置（如默认下载目录）"),
    "auth": ("auth", "凭据管理：登录录入 / 退出 / 查看状态"),
    "course": ("course", "课程操作（列出可同步的课程）"),
}

#: 扁平命令（无下级子命令）：模块里直接定义 ``<name>_cmd`` 函数，
#: 其余模块用 ``app = typer.Typer(...)`` 声明为命令组
FLAT_COMMANDS = frozenset({"run", "doctor"})


def _notify_update_after_command(result: Any, **_: Any) -> Any:
    """普通命令成功后检查更新；提示失败绝不改变命令结果。"""
    ctx = click.get_current_context(silent=True)
    app_ctx = ctx.obj if ctx is not None else None
    if app_ctx is None or getattr(app_ctx, "quiet", False):
        return result
    try:
        from bb_sync.core.update import notify_if_outdated

        notify_if_outdated(app_ctx.console)
    except Exception:  # 更新检查是附加功能，不能影响正常命令
        pass
    return result


def _as_click_group(obj: object) -> Any:
    """把命令模块导出的 Typer 实例转成 Click 命令组。

    Typer 实例不是 Click 组，需要 ``typer.main.get_command`` 转换
    （延迟到此处调用，避免在模块顶层导入 typer）。
    """
    import typer

    return typer.main.get_command(obj)  # type: ignore[arg-type]


def _module_command(mod: ModuleType, cmd_name: str) -> click.Command | click.Group:
    """取出命令模块对外暴露的命令对象（统一为 ``click.Command``）。

    模块统一导出 ``app = typer.Typer(...)``，由本函数按命令类型解包：

    - **扁平命令**（``FLAT_COMMANDS``，如 run / doctor）：模块里只有一条与命令同名的
      子命令，这里把它取出来直接平铺到根下，得到 ``bb-sync run [OPTIONS]``，
      而不是多余的 ``bb-sync run run``
    - **命令组**（config / auth / course）：整体作为组挂上，
      保留 ``bb-sync config set`` / ``bb-sync course list`` 这样的两级形态
    """
    app = getattr(mod, "app", None)
    if app is None:
        raise TypeError(f"命令模块 {mod.__name__!r} 缺少 app（Typer 实例）")
    group = _as_click_group(app)

    if cmd_name in FLAT_COMMANDS:
        return _unwrap_flat(group, mod.__name__, cmd_name)
    return _as_real_group(group, mod.__name__, cmd_name)


def _unwrap_flat(group: click.Command, mod_name: str, cmd_name: str) -> click.Command:
    """扁平命令：从（可能被折叠的）Typer 命令里取出唯一那条命令。

    Typer 会把「只有一个子命令」的 app 直接折叠成那条命令（此时没有
    ``commands`` 属性），两种情况都要兼容，并统一把名字改成 ``cmd_name``，
    避免出现 ``bb-sync run run``。
    """
    if not hasattr(group, "commands"):
        group.name = cmd_name
        return group

    commands = getattr(group, "commands", None) or {}
    if len(commands) != 1:
        raise TypeError(f"扁平命令模块 {mod_name!r} 应恰好声明 1 条命令，实际 {len(commands)} 条")
    inner = next(iter(commands.values()))
    inner.name = cmd_name
    return inner


def _as_real_group(group: click.Command, mod_name: str, cmd_name: str) -> click.Command:
    """命令组：保证返回的对象带 ``commands``，且名字与注册名一致。

    **不能靠 ``isinstance(group, click.Group)`` 判断**——Typer 0.27 的组类型
    ``TyperGroup`` 并不继承 ``click.Group``（它继承 ``typer._click.core.Command``）。
    因此这里用「有没有 ``commands`` 属性」作为「是不是命令组」的判据：

    - 有 ``commands`` → 真命令组（如 config / auth），只把名字对齐注册名即可
    - 没有 → 被 Typer 折叠成了单条命令（当 app 里的命令名与 app 名同名时会发生），
      此时把它按真实子命令名重新包进一个 Click 组，否则
      ``bb-sync course list`` 里的 ``list`` 会被当成多余参数报 UsageError
    """
    if hasattr(group, "commands"):
        group.name = cmd_name
        return group

    # 被折叠成单条命令：用它的 name（真实子命令名）重建一个组
    inner_name = group.name or ""
    wrapper = click.Group(name=cmd_name, help=SUBCOMMANDS.get(cmd_name, ("", ""))[1])
    wrapper.add_command(group, inner_name)
    return wrapper


class LazyGroup(click.Group):
    """按需导入子命令模块的 Click 命令组。

    注册时只记下「命令名 → 模块名 + 一句话帮助」，真正解析到该命令时才
    ``import bb_sync.commands.<模块>``。这样 ``--version`` / ``--help`` 都不必
    为 playwright、pydantic、keyring 的导入成本买单。

    关键点：``--help`` 渲染命令列表时 Click 会逐个 ``get_command``；若在那里
    做真实导入，help 就退化成全量加载。因此 :meth:`get_command` 分两级——
    渲染帮助走 :meth:`_placeholder`（零导入），只有真正要执行命令时才导入。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loaded: set[str] = set()
        #: 正在渲染根帮助（此时只需命令名与一句话说明，不必导入模块）
        self._listing = False

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(SUBCOMMANDS)

    @staticmethod
    def _placeholder(name: str) -> click.Command:
        """给根帮助列表用的占位命令：只有元信息，不触发任何导入。"""
        return click.Command(name, help=SUBCOMMANDS[name][1], params=[], callback=lambda: None)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """渲染根帮助的命令列表时只读本地元信息，不导入任何命令模块。"""
        self._listing = True
        try:
            super().format_commands(ctx, formatter)
        finally:
            self._listing = False

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name not in SUBCOMMANDS:
            return None
        # 渲染根帮助列表：给占位符即可，避免为了列一行说明把 playwright 拖进来。
        # 但用户显式 `bb-sync <cmd> --help` 时必须给真命令，所以用 _listing 区分。
        if self._listing:
            return self.commands.get(cmd_name) or self._placeholder(cmd_name)

        module = SUBCOMMANDS[cmd_name][0]
        if module in self._loaded:
            return self.commands.get(cmd_name)
        self._loaded.add(module)

        mod = importlib.import_module(f"bb_sync.commands.{module}")
        sub = _module_command(mod, cmd_name)
        sub.help = SUBCOMMANDS[cmd_name][1]
        self.add_command(sub, cmd_name)
        return self.commands.get(cmd_name)


def _build_cli() -> LazyGroup:
    """构建带全局选项的根命令组。"""

    @click.group(
        cls=LazyGroup,
        name="bb-sync",
        result_callback=_notify_update_after_command,
        help="Blackboard 课程资源自动同步（ADFS SSO + Steel 浏览器后端）。",
        invoke_without_command=True,
        # 不用 no_args_is_help：那会让「无参数」以退出码 2 结束。
        # 本工具约定无参数 = 显示帮助 + 退出码 0（见下方回调）。
        no_args_is_help=False,
        context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100},
    )
    @click.option("--json", "json_mode", is_flag=True, help="结果以 JSON 输出到 stdout")
    @click.option("--quiet", "-q", "quiet", is_flag=True, help="抑制 stderr 上的普通日志")
    @click.option("--verbose", "-v", "verbose", is_flag=True, help="stderr 上输出调试信息")
    @click.option(
        "--config",
        "config_file",
        default=None,
        help="指定配置文件路径（默认 CWD，其次 ~/.bb-sync/）",
    )
    @click.version_option(
        __version__, "--version", prog_name="bb-sync", message="%(prog)s %(version)s"
    )
    @click.pass_context
    def cli(
        ctx: click.Context,
        json_mode: bool,
        quiet: bool,
        verbose: bool,
        config_file: str | None,
    ) -> None:
        """Blackboard 课程资源自动同步。"""
        from bb_sync.context import Context as _Ctx

        ctx.obj = _Ctx(
            json_mode=json_mode,
            quiet=quiet,
            verbose=verbose,
            config_file=config_file,
        )
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())
            ctx.exit(ExitCode.SUCCESS)

    return cli  # type: ignore[return-value]


def _clickish_exception(exc: BaseException) -> bool:
    """判断是否为 Click / Typer 的「用法类」异常。

    **不能直接 ``isinstance(exc, click.ClickException)``**：Typer ≥0.27 内置了
    一份自己的 ``_click``（``typer._click.exceptions``），它与官方 ``click`` 是
    两套互不继承的类层级（``typer.ClickException`` 不是 ``click.ClickException``
    的子类）。若只按官方类型捕获，``No such option`` 这类用法错误会掉进兜底分支，
    既打印堆栈又返回错误的退出码 1。

    这类异常的共性：带 ``exit_code`` 属性、且定义 ``show()``（官方 click）或
    ``format_message()``（typer 内置 click）用于渲染错误信息，故按鸭子类型判定。
    """
    return hasattr(exc, "exit_code") and (hasattr(exc, "show") or hasattr(exc, "format_message"))


def main() -> None:
    """控制台脚本入口：统一异常处理与退出码。"""
    import typer

    cli = _build_cli()
    try:
        cli.main(standalone_mode=False)
    except (click.exceptions.Abort, KeyboardInterrupt):
        sys.stderr.write("\n已中断\n")
        raise SystemExit(ExitCode.INTERRUPTED) from None
    except typer.Exit as exc:
        # --help / --version 的正常退出，携带自己的退出码（通常 0）
        raise SystemExit(exc.exit_code) from None
    except Exception as exc:
        # 用法类异常（官方 click 与 typer 内置 click 都覆盖）：渲染信息后按其退出码退出
        if _clickish_exception(exc):
            if hasattr(exc, "show"):
                exc.show()  # type: ignore[attr-defined]
            else:
                sys.stderr.write(str(exc) + "\n")
            raise SystemExit(int(getattr(exc, "exit_code", ExitCode.USAGE_ERROR))) from None
        if isinstance(exc, BbSyncError):
            from bb_sync.core.output import Console

            console = Console()
            console.error(exc.message)
            if exc.hint:
                console.hint(exc.hint)
            raise SystemExit(exc.exit_code) from None
        if isinstance(exc, SystemExit):
            raise
        # 兜底：非预期异常显示堆栈，方便报 issue
        sys.stderr.write(f"✗ 未预期的错误：{exc}\n")
        import traceback

        traceback.print_exc()
        raise SystemExit(exit_code_for(exc)) from None


if __name__ == "__main__":
    main()
