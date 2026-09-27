"""``bb-sync doctor`` —— 环境体检。

本模块导出的是**扁平命令**（``bb-sync doctor [OPTIONS]``）：
它在语义上没有下级子命令，多一层 ``doctor doctor`` 只是噪音。
"""

from __future__ import annotations

import typer

from bb_sync.context import Context
from bb_sync.core import doctor
from bb_sync.core.errors import EnvironmentError_

#: 模块对应的子命令名（供 cli.LazyGroup 识别）
SUBCOMMAND = "doctor"
HELP = "体检：检查前置条件（git / Node / 浏览器 / 网络等）"

#: 单命令 Typer app：借 Typer 从函数签名推导参数，随后由 cli 层取出那条命令平铺
app = typer.Typer(help=HELP, add_completion=False)


@app.command("doctor")
def doctor_cmd(
    ctx: typer.Context,
    list_only: bool = typer.Option(False, "--list", help="只列出检查项，不实际执行"),
    skip_network: bool = typer.Option(False, "--skip-network", help="跳过网络可达性检查"),
) -> None:
    """体检：检查前置条件（git / Node / 浏览器 / 网络等）。"""
    app_ctx: Context = ctx.obj
    console = app_ctx.console

    if list_only:
        names = [fn.__name__.removeprefix("check_") for fn in doctor.CHECKS]
        console.table(["检查项"], [[n] for n in names], json_data={"checks": names})
        return

    results = doctor.run_doctor(console, skip_network=skip_network)

    if app_ctx.json_mode:
        console.emit(
            {
                "ok": not any(r.required and not r.ok for r in results),
                "checks": [
                    {
                        "label": r.label,
                        "ok": r.ok,
                        "detail": r.detail,
                        "hint": r.hint,
                        "required": r.required,
                    }
                    for r in results
                ],
            }
        )

    missing = [r for r in results if r.required and not r.ok]
    if missing:
        raise EnvironmentError_(
            f"环境体检未通过：{'、'.join(r.label for r in missing)} 缺失",
            "按上方提示逐项修复后重试",
        )


__all__ = ["HELP", "SUBCOMMAND", "app", "doctor_cmd"]
