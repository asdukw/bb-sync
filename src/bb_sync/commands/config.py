"""``bb-sync config`` —— 查看与修改配置文件（编辑时保留原有注释）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import typer
import yaml

from bb_sync import paths
from bb_sync.context import Context
from bb_sync.core import config_store
from bb_sync.core.config import (
    DEFAULT_CONFIG_TEXT,
    Settings,
    config_target,
    dump_settings,
    ensure_config_file,
    load_settings,
)
from bb_sync.core.errors import ConfigError, NotFoundError

app = typer.Typer(
    help="查看 / 修改配置（如默认下载目录）", no_args_is_help=True, add_completion=False
)


def _target(ctx: typer.Context) -> Path:
    """本次操作的目标配置文件。"""
    return config_target(ctx.obj.config_file)


def _require(ctx: typer.Context) -> Path:
    """取配置文件路径，不存在则报错。"""
    path = _target(ctx)
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}", "运行 bb-sync config init 生成默认配置")
    return path


def _root_for_home(raw: str, target: Path) -> str:
    """把目标配置里的 root 转成用户级配置中语义相同的值。

    相对路径在不同配置文件中的基准目录不同；同步到 ``BB_SYNC_HOME`` 前必须先
    按目标配置文件所在目录解析成绝对路径，否则换目录运行时会指向错误位置。
    """
    parsed = config_store.parse_value(raw)
    if not isinstance(parsed, str):
        return raw
    value = parsed.strip()
    if not value:
        return raw
    root = Path(value).expanduser()
    if root.is_absolute() or PureWindowsPath(value).is_absolute():
        return raw
    return str((target.parent / root).resolve())


def _sync_home_root(
    target: Path,
    key: str,
    raw: str | None,
    enabled: bool,
) -> Path | None:
    """把 root 的改动同步到用户级配置，避免换目录运行时使用旧值。"""
    if not enabled or key != "root":
        return None
    home = paths.BB_SYNC_HOME / paths.CONFIG_NAME
    if home.resolve() == target.resolve():
        return None
    if raw is None:
        if not home.exists():
            return None
        try:
            config_store.apply_to_file(home, "root", None)
        except NotFoundError:
            return None
    else:
        ensure_config_file(home)
        config_store.apply_to_file(home, "root", _root_for_home(raw, target))
    return home


@app.command("path")
def path_cmd(ctx: typer.Context) -> None:
    """显示实际生效的配置文件路径。"""
    typer.echo(str(_target(ctx)))


@app.command("init")
def init(ctx: typer.Context) -> None:
    """生成一份默认配置文件（已存在时不覆盖）。"""
    app_ctx: Context = ctx.obj
    target = _target(ctx)
    created = ensure_config_file(target)
    if created:
        app_ctx.console.success(f"已生成默认配置 {target}")
    else:
        app_ctx.console.log(f"配置文件已存在，未改动：{target}")
    app_ctx.console.emit({"path": str(target), "created": created})


@app.command("show")
def show(ctx: typer.Context) -> None:
    """显示整份配置文件内容。"""
    app_ctx: Context = ctx.obj
    target = _target(ctx)
    if not target.exists():
        raise ConfigError(f"配置文件不存在：{target}", "运行 bb-sync config init 生成默认配置")
    text = target.read_text(encoding="utf-8")
    if app_ctx.json_mode:
        settings, _ = load_settings(target)
        app_ctx.console.emit(dump_settings(settings))
    else:
        app_ctx.console.print_raw(text)


@app.command("get")
def get(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="配置键，支持点号子键（如 root、course_dirs.CSC5010）"),
) -> None:
    """读取某一项配置。"""
    app_ctx: Context = ctx.obj
    target = _require(ctx)
    settings, _ = load_settings(target)
    node: object = dump_settings(settings) if not key else None

    if key:
        node = settings
        for part in key.split("."):
            if isinstance(node, dict):
                node = node.get(part) if part in node else None
            else:
                node = getattr(node, part, None)
            if node is None:
                raise NotFoundError(f"配置里没有：{key}")

    if isinstance(node, Settings):
        node = dump_settings(node)
    if app_ctx.json_mode:
        app_ctx.console.emit(node)
    elif isinstance(node, (dict, list)):
        app_ctx.console.print_raw(
            yaml.safe_dump(
                node, allow_unicode=True, default_flow_style=False, sort_keys=False
            ).rstrip()
        )
    else:
        app_ctx.console.print_raw(str(node))


@app.command("set")
def set_(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="配置键（如 root、course_dirs.CSC5010）"),
    value: str = typer.Argument(..., help="值（YAML 标量或列表，含空格时加引号）"),
    sync_home: bool = typer.Option(
        True,
        "--sync-home/--no-sync-home",
        help="修改 root 时同步更新用户级配置",
    ),
) -> None:
    """写入一项配置（永久生效）；root 默认同步到用户级配置。"""
    app_ctx: Context = ctx.obj
    config_store.parse_value(value)  # 校验 YAML 合法性
    target = _target(ctx)
    ensure_config_file(target)
    action = config_store.apply_to_file(target, key, value)
    synced = _sync_home_root(target, key, value, sync_home)
    app_ctx.console.success(f"已写入 {target}")
    if synced is not None:
        app_ctx.console.success(f"已同步用户配置 {synced}")
    payload: dict[str, str] = {
        "path": str(target),
        "action": action,
        "key": key,
        "value": value,
    }
    if synced is not None:
        payload["synced_path"] = str(synced)
    app_ctx.console.emit(payload)


@app.command("unset")
def unset(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="配置键（如 root、course_dirs.CSC5010）"),
    sync_home: bool = typer.Option(
        True,
        "--sync-home/--no-sync-home",
        help="删除 root 时同步更新用户级配置",
    ),
) -> None:
    """删除某项配置（恢复内置默认值）；root 默认同步到用户级配置。"""
    app_ctx: Context = ctx.obj
    target = _require(ctx)
    action = config_store.apply_to_file(target, key, None)
    synced = _sync_home_root(target, key, None, sync_home)
    app_ctx.console.success(f"{action}（{target}）")
    if synced is not None:
        app_ctx.console.success(f"已同步用户配置 {synced}")
    payload: dict[str, str] = {"path": str(target), "action": action, "key": key}
    if synced is not None:
        payload["synced_path"] = str(synced)
    app_ctx.console.emit(payload)


@app.command("edit")
def edit(ctx: typer.Context) -> None:
    """用系统默认编辑器打开配置文件。"""
    app_ctx: Context = ctx.obj
    target = _target(ctx)
    ensure_config_file(target)
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.call([os.environ.get("EDITOR", "vi"), str(target)])
    app_ctx.console.emit({"path": str(target), "opened": True})


#: ``DEFAULT_CONFIG_TEXT`` 在 help 中被引用，导入即用，避免 linter 报未使用
_ = DEFAULT_CONFIG_TEXT


def register(root: typer.Typer) -> None:
    """挂载到根 app。"""
    root.add_typer(app, name="config")


__all__ = ["app", "register"]
