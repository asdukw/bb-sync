"""环境体检：逐项检查前置条件，输出 ✓/✗ 与修复提示。

必查项（缺失即视为环境不可用）: Python / git / Node.js≥22 / npm / 浏览器 / 网络。
可选展示项: 凭据 / Steel 后端状态。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from bb_sync import __version__
from bb_sync.browser import steel
from bb_sync.core.output import Console
from bb_sync.creds import load_credentials


@dataclass
class CheckResult:
    """单项体检结果。"""

    ok: bool
    label: str
    detail: str = ""
    hint: str = ""
    required: bool = True


def _tool_version(cmd: list[str]) -> str | None:
    """运行 ``<tool> --version``，返回首行；失败返回 None。"""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6, encoding="utf-8", errors="ignore"
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0].strip() if lines and out.returncode == 0 else None


def check_python() -> CheckResult:
    v = sys.version_info
    return CheckResult(
        (v.major, v.minor) >= (3, 11),
        "Python",
        f"{v.major}.{v.minor}.{v.micro}（需要 ≥3.11，uv 安装 bb-sync 时会自动准备）",
    )


def check_git() -> CheckResult:
    path = shutil.which("git")
    ver = _tool_version([path, "--version"]) if path else None
    return CheckResult(
        bool(ver),
        "git",
        ver or "未找到",
        "bb-sync 从 GitHub 安装需要 git：winget install --id Git.Git -e",
    )


def check_node() -> CheckResult:
    path = shutil.which("node")
    ver = _tool_version([path, "--version"]) if path else None
    ok = False
    if ver:
        m = re.match(r"v(\d+)", ver)
        ok = bool(m) and int(m.group(1)) >= 22
    return CheckResult(
        ok,
        "Node.js",
        ver or "未找到",
        "浏览器后端需要 Node.js ≥22（自带 npm）：winget install OpenJS.NodeJS.LTS",
    )


def check_npm() -> CheckResult:
    path = shutil.which("npm")
    ver = _tool_version([path, "--version"]) if path else None
    return CheckResult(
        bool(ver),
        "npm",
        ver or "未找到",
        "npm 随 Node.js 一起安装，单独缺失请重装 Node.js",
    )


def check_browser() -> CheckResult:
    custom = os.environ.get("CHROME_EXECUTABLE_PATH")
    if custom and Path(custom).exists():
        return CheckResult(True, "浏览器", f"自定义 {custom}")
    browser = steel.detect_browser()
    if browser:
        name = "Edge" if "edge" in Path(browser).name.lower() else "Chrome"
        return CheckResult(True, "浏览器", f"{name} ({browser})")
    return CheckResult(
        False,
        "浏览器",
        "未检测到 Chrome/Edge",
        "安装 Google Chrome 或 Microsoft Edge；非默认位置时设置 CHROME_EXECUTABLE_PATH",
    )


def check_network(retries: int = 3) -> CheckResult:
    """检查 github.com 可达性（首次部署 Steel 需要；代理抖动会瞬时失败，故重试）。"""
    last_err = ""
    for _ in range(retries):
        try:
            req = request.Request("https://github.com", headers={"User-Agent": "bb-sync"})
            request.urlopen(req, timeout=6)
            return CheckResult(True, "网络", "github.com 可达")
        except error.HTTPError:  # 有响应即视为可达（如 301/403）
            return CheckResult(True, "网络", "github.com 可达")
        except Exception as exc:
            last_err = str(exc)
            time.sleep(1.5)
    return CheckResult(
        False, "网络", f"github.com 不可达 ({last_err})", "代理用户请确认系统代理已开启"
    )


def check_credentials() -> CheckResult:
    try:
        creds = load_credentials()
    except Exception as exc:
        return CheckResult(
            False, "凭据", f"读取失败: {exc}", "运行 bb-sync auth login 重新录入", required=False
        )
    if creds.ok:
        return CheckResult(True, "凭据", f"已保存（{creds.source}）", required=False)
    return CheckResult(
        False, "凭据", "未配置", "运行 bb-sync auth login 录入学号密码", required=False
    )


def check_steel() -> CheckResult:
    if steel.healthy():
        return CheckResult(True, "Steel 后端", "服务端运行中", required=False)
    if steel.is_deployed():
        return CheckResult(True, "Steel 后端", "已部署（未运行，同步时自动启动）", required=False)
    return CheckResult(
        False,
        "Steel 后端",
        "未部署",
        "首次同步时自动下载部署（依赖 Node.js 与网络），无需手动操作",
        required=False,
    )


#: 体检项顺序即输出顺序
CHECKS = (
    check_python,
    check_git,
    check_node,
    check_npm,
    check_browser,
    check_network,
    check_credentials,
    check_steel,
)


def run_doctor(console: Console, *, skip_network: bool = False) -> list[CheckResult]:
    """执行全部体检项，逐项输出，返回结果列表。"""
    console.step(f"bb-sync {__version__} — doctor")
    console.log("")
    results: list[CheckResult] = []
    for fn in CHECKS:
        if skip_network and fn is check_network:
            continue
        result = fn()
        results.append(result)
        mark = "✓" if result.ok else "✗"
        line = f"[{mark}] {result.label}" + (f"  {result.detail}" if result.detail else "")
        if result.ok:
            console.log(line)
        else:
            console.error(line)
            if result.hint:
                console.hint(result.hint)

    required_fail = sum(1 for r in results if r.required and not r.ok)
    total_ok = sum(1 for r in results if r.ok)
    console.log(
        f"\n{total_ok}/{len(results)} 项通过"
        + ("，存在缺失项，请按提示修复" if required_fail else "")
    )
    return results


def environment_ok(console: Console) -> bool:
    """供 ``run`` 前置校验使用：必查项全过才返回 True。"""
    missing = [r for r in run_doctor(console) if r.required and not r.ok]
    return not missing


__all__ = ["CHECKS", "CheckResult", "environment_ok", "run_doctor"]
