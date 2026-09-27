"""``bb-sync due`` —— 整理全部课程的待办到 ``due.md``。"""

from __future__ import annotations

import typer

from bb_sync import paths
from bb_sync.context import Context
from bb_sync.core import service
from bb_sync.core.config import ensure_config_file
from bb_sync.core.errors import ConfigError

SUBCOMMAND = "due"
HELP = """扫描课程主页 Due / To Do 模块，生成按优先级排列的 due.md。

示例：
  bb-sync due                         扫描全部课程并写入 <root>/due.md
  bb-sync due --course CSC5010        只扫描指定课程（可重复）
  bb-sync due --root D:/University    临时指定输出目录
  bb-sync due --headed                有头模式（首次登录 / 需要人工过 MFA）
  bb-sync --json due                  输出机器可读 JSON
"""

app = typer.Typer(help=HELP.splitlines()[0], add_completion=False)


@app.command("due")
def due(
    ctx: typer.Context,
    headed: bool = typer.Option(False, "--headed", help="有头模式（首次登录/MFA 用）"),
    course: list[str] = typer.Option(
        None, "--course", help="只扫描指定课程代码（可多次），如 --course CSC5010"
    ),
    root: str = typer.Option(None, "--root", help="本次 due.md 的输出目录（临时覆盖配置）"),
) -> None:
    """生成课程待办清单。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console

    overrides = {"root": root, "include": course or None}
    settings, config_path = app_ctx.load_settings(cli_overrides=overrides)

    if not config_path.exists():
        if app_ctx.config_file:
            raise ConfigError(
                f"未找到配置文件：{config_path}", "检查 --config 路径，或省略该参数用默认配置"
            )
        ensure_config_file(paths.BB_SYNC_HOME / "config.yaml")
        settings, config_path = app_ctx.load_settings(cli_overrides=overrides)
        console.log(f"[info] 已生成默认配置 {config_path}（可按需修改）")

    options = service.DueOptions(
        settings=settings,
        root=service.plan_root(settings, config_path, root),
        console=console,
        headed=headed,
        courses=course or None,
    )
    stats = service.run_due(options)
    console.emit(stats.as_dict())


__all__ = ["HELP", "SUBCOMMAND", "app", "due"]
