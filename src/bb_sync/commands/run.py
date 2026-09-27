"""``bb-sync run`` —— 执行一次课程同步。

本模块导出的是**扁平命令**（``bb-sync run [OPTIONS]``）。
"""

from __future__ import annotations

import typer

from bb_sync import paths
from bb_sync.context import Context
from bb_sync.core import service
from bb_sync.core.config import ensure_config_file
from bb_sync.core.errors import ConfigError

#: 模块对应的子命令名
SUBCOMMAND = "run"
HELP = """执行一次课程同步（下载目录取配置里的 root，可用 --root 临时覆盖）。

示例：
  bb-sync run                        增量同步全部课程
  bb-sync run --course CSC5010       只同步指定课程（可重复）
  bb-sync run --dry-run              预览会下载什么，不实际下载
  bb-sync run --headed               有头模式（首次登录 / 需要人工过 MFA）
  bb-sync run --json                 输出机器可读 JSON，便于脚本消费
"""

#: 单命令 Typer app：借 Typer 从函数签名推导参数，随后由 cli 层取出那条命令平铺
app = typer.Typer(help=HELP.splitlines()[0], add_completion=False)


@app.command("run")
def run(
    ctx: typer.Context,
    headed: bool = typer.Option(False, "--headed", help="有头模式（首次登录/MFA 用）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只列出文件，不下载"),
    course: list[str] = typer.Option(
        None, "--course", help="只同步指定课程代码（可多次），如 --course CSC5010"
    ),
    root: str = typer.Option(None, "--root", help="本次同步的下载目录（临时覆盖配置里的 root）"),
) -> None:
    """执行一次课程同步。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console

    overrides = {"root": root, "include": course or None}
    settings, config_path = app_ctx.load_settings(cli_overrides=overrides)

    # 配置文件缺失时：显式指定的直接报错，其余自动落一份默认配置
    if not config_path.exists():
        if app_ctx.config_file:
            raise ConfigError(
                f"未找到配置文件：{config_path}", "检查 --config 路径，或省略该参数用默认配置"
            )
        ensure_config_file(paths.BB_SYNC_HOME / "config.yaml")
        settings, config_path = app_ctx.load_settings(cli_overrides=overrides)
        console.log(f"[info] 已生成默认配置 {config_path}（可按需修改）")

    options = service.SyncOptions(
        settings=settings,
        config_path=config_path,
        root=service.plan_root(settings, config_path, root),
        console=console,
        headed=headed,
        dry_run=dry_run,
        courses=course or None,
    )
    stats = service.run_sync(options)

    # 结果走 stdout：--json 时输出统计对象；人读模式下汇总已在 stderr 打印
    console.emit(stats.as_dict())


__all__ = ["HELP", "SUBCOMMAND", "app", "run"]
