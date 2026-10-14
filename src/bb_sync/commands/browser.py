"""``bb-sync browser`` —— 后台浏览器实例的状态与关闭。

浏览器按「分离进程」运行：一次同步结束后它继续待命，下次运行直接复用，
因此需要一个显式入口来看它在不在、以及把它关掉。

注意本模块名与 :mod:`bb_sync.browser` 包同名，但两者是不同包下的模块，
绝对导入下不会互相遮蔽。
"""

from __future__ import annotations

import typer

from bb_sync.browser import launcher
from bb_sync.context import Context

app = typer.Typer(
    help="浏览器后端：查看状态 / 关闭后台实例", no_args_is_help=True, add_completion=False
)


def _payload() -> dict:
    """把当前实例整理成 JSON 可序列化的结构。"""
    session = launcher.current_session()
    if session is None:
        return {
            "running": False,
            "browser": None,
            "executable": None,
            "mode": None,
            "port": None,
            "profile": None,
        }
    return {
        "running": True,
        "browser": session.browser.name,
        "executable": str(session.browser.path),
        "mode": "headed" if session.headed else "headless",
        "port": session.port,
        "profile": str(session.browser.profile_dir),
    }


@app.command("status")
def status(ctx: typer.Context) -> None:
    """查看后台浏览器实例是否在运行。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console
    data = _payload()

    if not data["running"]:
        if not console.json_mode:
            console.log("后台浏览器：未运行（下次同步会自动启动）")
        console.emit(data)
        return

    mode = "有窗口模式" if data["mode"] == "headed" else "静默无窗口"
    if console.json_mode:
        console.emit(data)
        return
    console.table(
        ["项目", "值"],
        [
            ["状态", "运行中"],
            ["浏览器", f"{data['browser']} ({data['executable']})"],
            ["模式", mode],
            ["端口", str(data["port"])],
            ["Profile", str(data["profile"])],
        ],
        json_data=data,
    )


@app.command("stop")
def stop(ctx: typer.Context) -> None:
    """关闭后台浏览器实例（登录态保留，下次运行仍免登录）。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console
    stopped = launcher.stop_browser(console)
    if not stopped:
        console.warn("没有正在运行的后台浏览器")
    console.emit({"stopped": stopped})


def register(root: typer.Typer) -> None:
    """挂载到根 app。"""
    root.add_typer(app, name="browser")


__all__ = ["app", "register", "status", "stop"]
