"""``bb-sync auth`` —— 凭据管理（login / logout / status）。

密码不通过命令行参数传递：那会进 shell history / 进程列表 / CI 日志。
一律走隐藏输入（getpass）并存入系统钥匙串，不在任何文件里落明文。
"""

from __future__ import annotations

import typer

from bb_sync.blackboard import login as login_mod
from bb_sync.context import Context
from bb_sync.creds import delete_credentials, load_credentials, mask

app = typer.Typer(
    help="凭据管理：登录录入 / 退出 / 查看状态", no_args_is_help=True, add_completion=False
)


@app.command("login")
def login(ctx: typer.Context) -> None:
    """交互式录入学号与密码，存入系统钥匙串（密码不回显）。"""
    app_ctx: Context = ctx.obj
    login_mod.prompt_credentials(app_ctx.console)
    app_ctx.console.emit({"saved": True, "backend": "keyring"})


@app.command("logout")
def logout(ctx: typer.Context) -> None:
    """从系统钥匙串删除已保存的凭据。"""
    app_ctx: Context = ctx.obj
    removed = delete_credentials()
    if removed:
        app_ctx.console.success("已从系统钥匙串删除凭据")
    else:
        app_ctx.console.warn("钥匙串中没有保存的凭据")
    app_ctx.console.emit({"removed": removed})


@app.command("status")
def status(ctx: typer.Context) -> None:
    """查看凭据当前配置状态（只显示脱敏后的账号）。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console

    creds = load_credentials()
    if creds.ok:
        payload = {
            "configured": True,
            "source": creds.source,
            "account": mask(creds.student_id, tail=3),
        }
        if console.json_mode:
            console.emit(payload)
        else:
            console.table(
                ["项目", "值"],
                [
                    ["状态", "已配置"],
                    ["来源", creds.source],
                    ["账号", mask(creds.student_id, tail=3)],
                ],
                json_data=payload,
            )
    else:
        payload = {"configured": False, "source": None, "account": None}
        if console.json_mode:
            console.emit(payload)
        else:
            console.warn("尚未配置凭据")
            console.hint("运行 bb-sync auth login 录入")
            console.emit(payload)


def register(root: typer.Typer) -> None:
    """挂载到根 app。"""
    root.add_typer(app, name="auth")


__all__ = ["app", "register"]
