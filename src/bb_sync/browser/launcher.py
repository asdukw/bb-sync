"""本机浏览器后端：直接驱动用户已安装的 Chrome / Edge（分离进程 + CDP）。

设计要点：

- **不引入额外运行时**：浏览器就用用户机器上已有的 Chrome / Edge，本模块用
  ``--remote-debugging-port=0`` 把它拉起来，Playwright 只当 CDP 客户端。因此
  既不需要 Node / npm，也不需要 ``playwright install`` 下载浏览器。
- **分离进程**：浏览器独立于 CLI 进程存活——终端关掉、命令报错都不会把它带走，
  下一次运行直接复用同一个实例（登录态与冷启动时间都省下来）。要真正关掉它，
  用 :func:`stop_browser`（``bb-sync browser stop``）。
- **登录态持久化**：``--user-data-dir`` 指向 ``BB_SYNC_HOME/.browser-profile/<浏览器>``
  下的独立 profile，与用户日常使用的浏览器配置互不干扰。
- **端口自发现**：Chrome 会把实际监听的端口写进 profile 目录下的
  ``DevToolsActivePort``，所以「端口被占用」「并发运行」都不需要额外协调。

运行期文件（profile、运行状态、日志）统一放 ``BB_SYNC_HOME``（默认 ``~/.bb-sync``），
路径在模块导入时快照；需要换目录请通过 ``BB_SYNC_HOME`` 在启动前设置。

本模块不直接 ``print``：诊断一律走注入的 :class:`~bb_sync.core.output.Console`
（stderr），保证 ``bb-sync run --json`` 的 stdout 依然干净。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib import request

from bb_sync import paths
from bb_sync.core.errors import EnvironmentError_
from bb_sync.core.output import Console

IS_WIN = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

PROFILE_ROOT = paths.BB_SYNC_HOME / ".browser-profile"
#: 当前实例的运行状态；``bb-sync browser status`` 与 doctor 都读它
STATE_FILE = PROFILE_ROOT / "session.json"
BROWSER_LOG = paths.BB_SYNC_HOME / "browser.log"

#: Chrome 自己写下的「DevTools 实际端口 + WebSocket 路径」文件名（我们只读不写）
PORT_FILE_NAME = "DevToolsActivePort"

#: 默认 console；命令层应传入自己的 console，模块级调用才保持静默
_SILENT = Console(quiet=True)

#: Chrome 一旦以 ``--remote-debugging-port`` 启动，``navigator.webdriver`` 就是 true
#: （实测：不经 Playwright、直接读 CDP 也是 true）。站点常用它判定「被自动化控制」，
#: 这里抹掉，与旧 Steel 后端的反检测能力保持对齐。
_STEALTH_JS = 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'


@dataclass(frozen=True)
class Browser:
    """一个可用的本机浏览器。"""

    name: str  # "chrome" / "edge"，同时用作 profile 子目录名
    path: Path

    @property
    def label(self) -> str:
        """给用户看的名字。"""
        return "Edge" if self.name == "edge" else "Chrome"

    @property
    def profile_dir(self) -> Path:
        """该浏览器的专用 profile 目录（登录态就存在这里）。"""
        return PROFILE_ROOT / self.name


@dataclass(frozen=True)
class Session:
    """一个正在运行的浏览器实例。"""

    browser: Browser
    port: int
    ws_url: str
    headed: bool

    @property
    def mode_label(self) -> str:
        """显示模式的中文说明。"""
        return "有窗口模式" if self.headed else "静默无窗口"


def browser_install_hint() -> str:
    """按平台给出浏览器安装方式。"""
    if IS_MACOS:
        return "macOS 可运行 brew install --cask google-chrome 或 microsoft-edge"
    if IS_WIN:
        return (
            "安装 Google Chrome 或使用 Windows 自带的 Edge；非默认位置时设置 CHROME_EXECUTABLE_PATH"
        )
    return "安装 Google Chrome、Chromium 或 Microsoft Edge；非默认位置时设置 CHROME_EXECUTABLE_PATH"


def _browser_candidates() -> tuple[tuple[str, str], ...]:
    """按当前平台列出 Chrome / Edge 的常见安装位置（Chrome 全部优先于 Edge）。"""
    if IS_WIN:
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),  # 用户级安装（无管理员权限时常见）
        ]
        rels = (
            ("chrome", r"Google\Chrome\Application\chrome.exe"),
            ("edge", r"Microsoft\Edge\Application\msedge.exe"),
        )
        return tuple((name, str(Path(root) / rel)) for name, rel in rels for root in roots if root)
    if IS_MACOS:
        apps = (Path("/Applications"), Path.home() / "Applications")
        mac_rels = (
            ("chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        )
        return tuple((name, str(app / rel)) for name, rel in mac_rels for app in apps)
    return (
        ("chrome", "/usr/bin/google-chrome"),
        ("chrome", "/usr/bin/google-chrome-stable"),
        ("chrome", "/usr/bin/chromium"),
        ("chrome", "/usr/bin/chromium-browser"),
        ("edge", "/usr/bin/microsoft-edge"),
        ("edge", "/usr/bin/microsoft-edge-stable"),
    )


def _browser_name(path: Path) -> str:
    """从可执行文件名判断是哪家的浏览器（决定用哪个 profile 目录）。"""
    return "edge" if "edge" in path.name.lower() else "chrome"


def detect_browser(explicit: str | None = None) -> Browser | None:
    """探测可用的本机浏览器：显式指定 > ``CHROME_EXECUTABLE_PATH`` > 常见路径。

    显式指定（参数或环境变量）但文件不存在时直接报错，不静默回退到别的浏览器——
    否则用户会以为自己配的那份生效了。
    """
    override = explicit or os.environ.get("CHROME_EXECUTABLE_PATH")
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return Browser(_browser_name(candidate), candidate)
        raise EnvironmentError_(
            f"指定的浏览器不存在：{candidate}",
            "检查 CHROME_EXECUTABLE_PATH 是否指向 chrome.exe / msedge.exe 的真实路径",
        )
    for name, raw in _browser_candidates():
        if Path(raw).exists():
            return Browser(name, Path(raw))
    return None


def _launch_args(browser: Browser, headed: bool) -> list[str]:
    """组装浏览器启动参数（纯函数，便于断言）。"""
    args = [
        str(browser.path),
        "--remote-debugging-port=0",  # 0 = 让 Chrome 自己挑空闲端口
        f"--user-data-dir={browser.profile_dir}",
    ]
    if not headed:
        args.append("--headless=new")
    args += [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--window-size=1920,1080",
        "about:blank",
    ]
    return args


def _read_port_file(profile: Path) -> tuple[int, str] | None:
    """读 ``DevToolsActivePort``：首行是端口，次行是 WebSocket 路径。"""
    try:
        lines = (profile / PORT_FILE_NAME).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < 2:
        return None
    try:
        return int(lines[0].strip()), lines[1].strip()
    except ValueError:
        return None


# 本机回环必须绕过系统代理（用户环境常有 Clash 等），否则探测会被代理拒绝
_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))


def _probe(port: int, timeout: float = 2.0) -> dict | None:
    """访问 CDP 的 ``/json/version``；实例不在（端口关闭/非浏览器）则返回 None。"""
    try:
        with _NO_PROXY_OPENER.open(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _read_state() -> dict[str, object]:
    """读运行状态；缺失或损坏时返回空字典。"""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(session: Session, console: Console = _SILENT) -> None:
    """记录当前实例的运行状态（判断显示模式是否变化要用到它）。"""
    payload = {
        "tool": "bb-sync",
        "schema": 1,
        "browser": session.browser.name,
        "executable": str(session.browser.path),
        "profile": str(session.browser.profile_dir),
        "port": session.port,
        "headed": session.headed,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        console.warn(f"[browser] 写入运行状态失败（不影响使用）: {exc}")


def _clear_state() -> None:
    """删掉运行状态文件（实例已不存在时）。"""
    with contextlib.suppress(OSError):
        STATE_FILE.unlink(missing_ok=True)


def current_session() -> Session | None:
    """当前正在运行的实例；没有在跑（或探测不到）则返回 None。

    以 Chrome 自己写的端口文件为准，因此即使 ``session.json`` 丢了也能发现实例；
    显示模式（有窗口 / 无窗口）只能从状态文件读，读不到就按无窗口处理。
    """
    try:
        browser = detect_browser()
    except EnvironmentError_:
        return None
    if browser is None:
        return None
    info = _read_port_file(browser.profile_dir)
    if info is None:
        return None
    port, ws_path = info
    if _probe(port) is None:
        return None
    return Session(
        browser, port, f"ws://127.0.0.1:{port}{ws_path}", bool(_read_state().get("headed"))
    )


def _launch(console: Console, browser: Browser, headed: bool, wait: float = 60.0) -> Session:
    """以分离进程启动浏览器，等 DevTools 端口就绪后返回会话。"""
    profile = browser.profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    # 上次退出可能留下已失效的端口文件，先清掉，免得把死端口当成在跑的实例
    with contextlib.suppress(OSError):
        (profile / PORT_FILE_NAME).unlink(missing_ok=True)

    BROWSER_LOG.parent.mkdir(parents=True, exist_ok=True)
    # 日志句柄交给子进程即可，父进程关闭不影响子进程继续写
    with open(BROWSER_LOG, "a", encoding="utf-8") as logf:
        flags = 0
        if IS_WIN:
            # CREATE_NO_WINDOW：不加会闪出控制台窗口（DETACHED_PROCESS 下 Chrome 的
            # 子进程仍可能弹窗）；POSIX 类型存根里没有这个常量，用 getattr 兜底
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        # 分离：Windows 靠 CREATE_* 标志，POSIX 靠独立会话，
        # 这样关掉终端、命令退出都不会把浏览器一起带走
        subprocess.Popen(
            _launch_args(browser, headed),
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=not IS_WIN,
        )

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        info = _read_port_file(profile)
        if info is not None and _probe(info[0]) is not None:
            port, ws_path = info
            session = Session(browser, port, f"ws://127.0.0.1:{port}{ws_path}", headed)
            _write_state(session, console)
            return session
        time.sleep(0.25)
    raise EnvironmentError_(
        f"{browser.label} 启动超时（{wait:.0f} 秒）",
        f"查看日志 {BROWSER_LOG}；若浏览器被安全软件或组策略拦截，"
        f"可先手动打开一次 {browser.path} 再重试",
    )


def ensure_browser(console: Console = _SILENT, headed: bool = False) -> Session:
    """确保有一个可用的浏览器实例并返回它（已经在跑就复用）。

    复用是「分离进程」的收益：省掉冷启动，也让登录态的浏览器会话连续。
    但同一个 profile 不能同时开两个实例——参数不一致时后启动的进程只会把命令转发给
    旧实例然后自己退出，所以显示模式或浏览器变化时必须先关掉旧的。
    """
    browser = detect_browser()
    if browser is None:
        raise EnvironmentError_("未检测到 Chrome 或 Edge", browser_install_hint())

    live = current_session()
    if live is not None:
        if live.browser.path == browser.path and live.headed == headed:
            console.log(
                f"[browser] 复用已在运行的 {browser.label}（{live.mode_label}，端口 {live.port}）"
            )
            _write_state(live, console)
            return live
        console.step(f"[browser] 运行方式已变化，重启 {live.browser.label} ...")
        stop_browser(console)

    console.step(f"[browser] 启动 {browser.label}（{browser.profile_dir}）...")
    session = _launch(console, browser, headed)
    console.success(f"[browser] {browser.label} 就绪（{session.mode_label}，端口 {session.port}）")
    return session


@contextlib.contextmanager
def browser_session(
    console: Console = _SILENT, *, headed: bool = False, keep_open: bool = False
) -> Iterator[Session]:
    """借一个浏览器来跑一次命令，并在结束时按约定收尾。

    默认**跑完就关**——命令结束（成功或失败）都不留后台进程，用户机器上不会
    莫名其妙多出一个浏览器在跑。``keep_open=True`` 则保留实例，用于手动过 MFA、
    连续多次运行或调试；之后用 :func:`stop_browser`（``bb-sync browser stop``）关闭。

    无论哪种模式，浏览器进程本身都是分离的：命令中途终端被关掉，它也不会被带走。
    """
    session = ensure_browser(console, headed)
    try:
        yield session
    finally:
        if keep_open:
            console.log("[browser] 已保留后台浏览器（--keep-open）；关闭：bb-sync browser stop")
        else:
            stop_browser(console)


def stop_browser(console: Console = _SILENT) -> bool:
    """关闭后台浏览器实例（登录态保留在 profile）；没在跑则返回 False。

    走 CDP 的 ``Browser.close``，让 Chrome 自己正常退出并把 profile 落盘。
    不按 PID 杀进程：状态文件里的 PID 可能已被系统回收，误杀无关进程的代价太大。
    """
    session = current_session()
    if session is None:
        _clear_state()
        return False

    from playwright.sync_api import sync_playwright  # 延迟导入，保住 --help 冷启动

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session.ws_url, timeout=20000)
            browser.new_browser_cdp_session().send("Browser.close")
    except Exception as exc:
        console.warn(f"[browser] 关闭浏览器失败: {exc}")
        return False

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _probe(session.port, timeout=1.0) is not None:
        time.sleep(0.25)
    with contextlib.suppress(OSError):
        (session.browser.profile_dir / PORT_FILE_NAME).unlink(missing_ok=True)
    _clear_state()
    console.log("[browser] 已关闭后台浏览器（登录态保留在 profile）")
    return True


def connect(p, session: Session):
    """通过 CDP 接入分离的浏览器，返回 ``(browser, context, page)``。

    调用方**不要**用 ``browser.close()`` 收尾：对 ``connect_over_cdp`` 得到的
    browser 它只断开 CDP 连接，不会结束进程（实测）。要真关掉实例请用
    :func:`stop_browser`；正常跑完直接退出即可，浏览器按设计继续活着。
    """
    # Playwright 也会读取系统代理，回环地址需要显式豁免
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,*"
    os.environ["no_proxy"] = "127.0.0.1,localhost,*"
    browser = p.chromium.connect_over_cdp(session.ws_url, timeout=60000)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    ctx.add_init_script(_STEALTH_JS)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return browser, ctx, page


__all__ = [
    "BROWSER_LOG",
    "Browser",
    "PROFILE_ROOT",
    "STATE_FILE",
    "Session",
    "browser_install_hint",
    "browser_session",
    "connect",
    "current_session",
    "detect_browser",
    "ensure_browser",
    "stop_browser",
]
