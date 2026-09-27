"""
Steel 浏览器后端（本地 Node 服务端 + CDP 接入 + 会话持久化）
=========================================================
- 后端以独立进程常驻（默认无窗口静默），Steel 自带 stealth/指纹伪装，规避站点反爬检测
- 通过 CDP 与 Playwright 对接，脚本完全静默运行
- 使用持久化 Chrome profile（userDataDir + persist），登录态跨次运行保留
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

HERE = Path(__file__).resolve().parent
STEEL_DIR = HERE / ".steel" / "api"
STEEL_LOG = HERE / ".steel" / "steel.log"
STEEL_URL = os.environ.get("STEEL_URL", "http://127.0.0.1:3000")
PROFILE_DIR = HERE / ".browser-profile" / "steel-chrome"

IS_WIN = sys.platform.startswith("win")


def _node_bin() -> str:
    """定位 Node.js：优先 PATH，其次 nvm4w 默认安装位置"""
    found = shutil.which("node")
    if found:
        return found
    for cand in (r"C:\nvm4w\nodejs\node.exe",):
        if Path(cand).exists():
            return cand
    raise RuntimeError("未找到 Node.js，请先安装 Node.js（或将其加入 PATH）")


# 本机回环必须绕过系统代理（用户环境有 Clash Verge），否则探测会被代理拒绝
_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))


def _http(method: str, path: str, body: dict | None = None, timeout: float = 10):
    url = f"{STEEL_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw) if raw else {}


def healthy() -> bool:
    try:
        _http("GET", "/v1/sessions", timeout=3)
        return True
    except Exception:
        return False


def start_server(headed: bool, wait: int = 120) -> None:
    """后台启动 Steel 服务端（独立进程，不阻塞）"""
    if not STEEL_DIR.exists():
        raise RuntimeError(
            f"Steel 源码缺失: {STEEL_DIR}\n请先执行: git clone --depth 1 "
            f"https://github.com/steel-dev/steel-browser .steel"
        )
    # npm workspaces 会把依赖提升到 .steel/node_modules
    tsx = next(
        (
            d / "node_modules" / "tsx" / "dist" / "cli.mjs"
            for d in (STEEL_DIR, STEEL_DIR.parent)
            if (d / "node_modules" / "tsx" / "dist" / "cli.mjs").exists()
        ),
        None,
    )
    if tsx is None:
        raise RuntimeError("Steel 依赖未安装，请在 .steel 目录下执行 npm install")

    env = dict(os.environ)
    env["CHROME_HEADLESS"] = "false" if headed else "true"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = STEEL_URL.rsplit(":", 1)[-1]

    STEEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    # 日志句柄需随后台进程长期存活，不适用 context manager
    logf = open(STEEL_LOG, "a", encoding="utf-8")  # noqa: SIM115
    flags = 0
    if IS_WIN:
        # Windows 专属创建标志（POSIX 类型存根中不存在，用 getattr 兜底通过跨平台类型检查）
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    subprocess.Popen(
        [_node_bin(), str(tsx), "src/index.ts"],
        cwd=str(STEEL_DIR),
        env=env,
        stdout=logf,
        stderr=logf,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )

    for _ in range(wait):
        time.sleep(1)
        if healthy():
            return
    raise RuntimeError(f"Steel 服务端启动超时，请查看日志: {STEEL_LOG}")


def ensure_server(headed: bool) -> None:
    if healthy():
        print("[steel] 已在运行 ✓", flush=True)
        return
    print("[steel] 启动服务端 ...", flush=True)
    start_server(headed)
    print("[steel] 服务端就绪 ✓", flush=True)


def create_session(headed: bool) -> dict:
    """创建（或复用）浏览器会话；persist + userDataDir 保证登录态留存"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "persist": True,
        "userDataDir": str(PROFILE_DIR),
        "blockAds": True,
        "dimensions": {"width": 1920, "height": 1080},
    }
    try:
        data = _http("POST", "/v1/sessions", payload, timeout=60)
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"创建 Steel 会话失败: {e.code} {detail}") from e
    print(
        f"[steel] 会话已创建: {data.get('id')} {'(有窗口模式)' if headed else '(静默无窗口)'}",
        flush=True,
    )
    return data


def release_session(session: dict) -> None:
    sid = session.get("id")
    if not sid:
        return
    try:
        _http("DELETE", f"/v1/sessions/{sid}", timeout=15)
        print("[steel] 会话已释放（登录态保留在 profile）", flush=True)
    except Exception:
        # 会话可能已被服务端自动回收，不影响持久化 profile 里的登录态
        print("[steel] 会话已结束（登录态保留在 profile）", flush=True)


def connect(p, session: dict):
    """通过 CDP 接入 Steel 浏览器，返回 (browser, context, page)"""
    # Playwright 也会读取系统代理，回环地址需要显式豁免
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,*"
    os.environ["no_proxy"] = "127.0.0.1,localhost,*"
    ws = session.get("websocketUrl") or STEEL_URL.replace("http", "ws")
    if not ws.endswith("/"):
        ws += "/"
    browser = p.chromium.connect_over_cdp(ws, timeout=60000)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return browser, ctx, page
