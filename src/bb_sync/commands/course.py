"""``bb-sync course`` —— 课程相关操作（当前提供 list）。"""

from __future__ import annotations

import typer

from bb_sync.context import Context
from bb_sync.core import service

app = typer.Typer(help="课程操作（列出可同步的课程）", no_args_is_help=True, add_completion=False)


@app.command("list")
def list_cmd(
    ctx: typer.Context,
    course: list[str] = typer.Option(None, "--course", help="只看指定课程代码（可多次）"),
) -> None:
    """列出账号下可同步的课程（不下载任何文件）。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console
    settings, config_path = app_ctx.load_settings(cli_overrides={"include": course or None})
    options = service.SyncOptions(
        settings=settings,
        config_path=config_path,
        root=service.plan_root(settings, config_path, None),
        console=console,
        headed=False,
        dry_run=True,
        courses=course or None,
    )
    courses = service.list_courses(options)
    records = [c.as_dict() for c in courses]
    console.table(
        ["课程代码", "标题", "本地文件夹"],
        [[c.code or "-", c.title, c.slug] for c in courses],
        json_data=records,
    )


def register(root: typer.Typer) -> None:
    """挂载到根 app。"""
    root.add_typer(app, name="course")


__all__ = ["app", "register"]
