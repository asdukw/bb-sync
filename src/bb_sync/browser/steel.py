"""Steel 浏览器后端（本地 Node 服务端 + CDP 接入 + 会话持久化）。

- 后端以独立进程常驻（默认无窗口静默），Steel 自带 stealth/指纹伪装，规避站点反爬检测
- 通过 CDP 与 Playwright 对接，脚本完全静默运行
- 使用持久化 Chrome profile（userDataDir + persist），登录态跨次运行保留

本模块不直接 print：所有诊断通过注入的 :class:`~bb_sync.core.output.Console`
走 stderr，因此 ``bb-sync run --json`` 时 stdout 依然干净。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

from bb_sync import paths
from bb_sync.core.errors import EnvironmentError_, NetworkError
from bb_sync.core.output import Console


def _resolve_steel_root() -> Path:
    """Steel 安装根目录：默认 ``~/.steel``，可用 ``BB_SYNC_STEEL_ROOT`` 覆盖。"""
    override = os.environ.get("BB_SYNC_STEEL_ROOT")
    return Path(override).expanduser() if override else Path.home() / ".steel"


# Steel 安装目录固定在用户主目录的 ~/.steel，不跟随 BB_SYNC_HOME：
# 不同版本、不同配置目录共用同一份后端，避免用户主目录出现多个 .steel。
# 旧版 BB_SYNC_HOME/.steel 会在 deploy() 时自动迁移/清理。
STEEL_ROOT = _resolve_steel_root()
STEEL_DIR = STEEL_ROOT / "api"
STEEL_LOG = STEEL_ROOT / "steel.log"
STEEL_URL = os.environ.get("STEEL_URL", "http://127.0.0.1:3000")
PROFILE_DIR = paths.BB_SYNC_HOME / ".browser-profile" / "steel-chrome"
# GitHub 源码包（免 git 依赖，无需 clone）
STEEL_ZIP_URL = "https://codeload.github.com/steel-dev/steel-browser/zip/refs/heads/main"

IS_WIN = sys.platform.startswith("win")

#: 默认 console，模块级函数在被命令层调用前可 keep 静默；命令层应传入自己的 console
_SILENT = Console(quiet=True)


def _node_bin() -> str:
    """定位 Node.js：优先 PATH，其次 nvm4w 默认安装位置。"""
    found = shutil.which("node")
    if found:
        return found
    for cand in (r"C:\nvm4w\nodejs\node.exe",):
        if Path(cand).exists():
            return cand
    raise EnvironmentError_(
        "未找到 Node.js",
        "浏览器后端需要 Node.js ≥22：winget install OpenJS.NodeJS.LTS",
    )


def _npm_bin() -> str | None:
    """定位 npm（与 Node.js 一起安装，测试可替换）。"""
    return shutil.which("npm")


def detect_browser() -> str | None:
    """探测本机 Chromium 内核浏览器：Chrome 优先，Edge 兜底。

    Steel 自带的探测只认 Chrome 标准路径，裸机（仅预装 Edge）会直接失败；
    这里提前探测，结果通过 ``CHROME_EXECUTABLE_PATH`` 传给 Steel 进程。
    """
    if IS_WIN:
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
    return next((c for c in candidates if Path(c).exists()), None)


def _legacy_steel_root() -> Path:
    """1.0.x 及更早版本的 Steel 安装目录（``BB_SYNC_HOME/.steel``）。"""
    return paths.BB_SYNC_HOME / ".steel"


def _migrate_legacy_root(console: Console) -> bool:
    """把旧版 ``BB_SYNC_HOME/.steel`` 迁到新根目录，返回是否迁移成功。

    只在新目录不存在时迁移，绝不覆盖用户已有安装；迁移失败不抛错，
    后续正常部署会重新下载，避免因旧目录损坏/被占用而无法启动。
    """
    legacy = _legacy_steel_root()
    if legacy == STEEL_ROOT or not legacy.exists() or STEEL_ROOT.exists():
        return False
    try:
        STEEL_ROOT.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(STEEL_ROOT))
    except OSError as exc:
        console.warn(f"[steel] 迁移旧安装目录失败（将重新部署）: {exc}")
        return False
    console.log(f"[steel] 已迁移旧安装目录: {legacy} -> {STEEL_ROOT}")
    return True


def _cleanup_legacy_root(console: Console) -> None:
    """新安装就绪后清理旧目录，确保电脑里只保留一份 Steel。"""
    legacy = _legacy_steel_root()
    if legacy == STEEL_ROOT or not legacy.exists():
        return
    try:
        shutil.rmtree(legacy)
    except OSError as exc:
        console.warn(f"[steel] 旧安装目录清理失败（可手动删除 {legacy}）: {exc}")
        return
    console.log(f"[steel] 已清理旧安装目录: {legacy}")


def is_deployed() -> bool:
    """Steel 后端是否已就绪（源码 + tsx 依赖都在）。"""
    return (STEEL_DIR / "src" / "index.ts").exists() and _find_tsx() is not None


def _steel_meta_path() -> Path:
    """部署元数据：标记 ``~/.steel`` 由 bb-sync 安装，并记录源码地址。"""
    return STEEL_ROOT / ".bb-sync.json"


def _read_steel_meta() -> dict[str, object]:
    try:
        data = json.loads(_steel_meta_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_steel_meta(console: Console) -> None:
    payload = {
        "tool": "bb-sync",
        "schema": 1,
        "zip_url": STEEL_ZIP_URL,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        _steel_meta_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        console.warn(f"[steel] 写入安装元数据失败（不影响使用）: {exc}")


def _needs_upgrade() -> bool:
    """bb-sync 装的旧版本（记录的源码地址与当前不一致）需要更新。"""
    meta = _read_steel_meta()
    return meta.get("tool") == "bb-sync" and meta.get("zip_url") != STEEL_ZIP_URL


def _unique_backup_path(kind: str) -> Path:
    """为安装目录生成同级的 ``.steel.<kind>-<时间戳>`` 备份路径。"""
    base = f"{STEEL_ROOT.name}.{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    candidate = STEEL_ROOT.with_name(base)
    seq = 2
    while candidate.exists():
        candidate = STEEL_ROOT.with_name(f"{base}-{seq}")
        seq += 1
    return candidate


def _quarantine_broken_root(console: Console) -> None:
    """残缺/损坏的目录不直接删：整体改名备份，保留排查线索。"""
    backup = _unique_backup_path("broken")
    try:
        shutil.move(str(STEEL_ROOT), str(backup))
    except OSError as exc:
        raise EnvironmentError_(
            f"Steel 目录不可用且无法重命名: {STEEL_ROOT}",
            "请先关闭正在运行的 Steel/Node 进程后重试；也可手动删除该目录",
        ) from exc
    console.warn(f"[steel] 检测到不可用的旧目录，已备份为: {backup}")


def _download_source(console: Console, tmp_dir: Path) -> Path:
    """下载源码 zip 并解压到 tmp_dir，返回解压出的项目根目录。"""
    import zipfile

    zip_path = tmp_dir / "steel-browser.zip"
    console.log("[steel] 下载 steel-browser 源码 ...")
    req = request.Request(STEEL_ZIP_URL, headers={"User-Agent": "bb-sync"})
    try:
        with request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as f:
            done = 0
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                if done // (5 * 1024 * 1024) != (done - len(chunk)) // (5 * 1024 * 1024):
                    console.log(f"[steel]   已下载 {done / 1048576:.0f} MB ...")
    except error.URLError as exc:
        raise NetworkError(
            f"下载 steel-browser 失败: {exc}",
            "请检查网络/代理是否可访问 github.com（Clash 用户请确认系统代理已开启）",
        ) from exc

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp_dir)
    source = tmp_dir / "steel-browser-main"
    if not source.is_dir():
        raise EnvironmentError_(
            "Steel 源码包结构异常（未找到 steel-browser-main 目录）",
            "可能是上游仓库结构变更，请反馈该问题后重试",
        )
    return source


def _install_source_tree(source: Path) -> Path | None:
    """把新源码放到 STEEL_ROOT，返回换下来的旧目录（没有则为 None）。

    已有安装先整体改名为 ``.steel.old-<时间戳>`` 再换入新版本：
    换入失败会自动回滚，旧备份由调用方在依赖装好后删除。
    """
    if not STEEL_ROOT.exists():
        shutil.move(str(source), str(STEEL_ROOT))
        return None

    backup = _unique_backup_path("old")
    try:
        shutil.move(str(STEEL_ROOT), str(backup))
    except OSError as exc:
        raise EnvironmentError_(
            f"更新 Steel 后端失败，旧目录被占用: {STEEL_ROOT}",
            "请先关闭正在运行的 Steel/Node 进程后重试",
        ) from exc
    try:
        shutil.move(str(source), str(STEEL_ROOT))
    except OSError as exc:
        _rollback_old_root(backup)
        raise EnvironmentError_(f"更新 Steel 后端失败: {exc}", "旧安装已回滚到原位置") from exc
    return backup


def _rollback_old_root(backup: Path) -> None:
    """升级失败时用备份恢复原安装（恢复不了也不掩盖原始错误）。"""
    shutil.rmtree(STEEL_ROOT, ignore_errors=True)
    if STEEL_ROOT.exists():
        return  # 新目录被占用删不掉时保留备份，避免把旧安装嵌套进去
    with contextlib.suppress(OSError):
        shutil.move(str(backup), str(STEEL_ROOT))


def _install_dependencies(npm: str, console: Console) -> None:
    """在 STEEL_DIR 执行 npm install；是否就绪以 tsx 为准。"""
    console.log("[steel] 安装 npm 依赖（首次需几分钟，请耐心等待）...")
    STEEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(STEEL_LOG, "a", encoding="utf-8") as logf:
        proc = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=str(STEEL_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        logf.write(proc.stdout or "")

    if proc.returncode != 0:
        console.warn(
            f"[steel] npm install 退出码 {proc.returncode}（若是 husky/prepare 报错可忽略）"
        )

    if not _find_tsx():
        tail = "\n".join((proc.stdout or "").splitlines()[-30:])
        raise EnvironmentError_(
            f"Steel 依赖安装失败，请查看日志: {STEEL_LOG}",
            f"npm 输出末尾:\n{tail}",
        )


def deploy(console: Console = _SILENT) -> None:
    """自动部署 Steel 后端：下载源码包 + 安装 npm 依赖（首次运行自动触发）。

    已存在的 ``~/.steel`` 按状态处理，绝不静默删除用户文件：

    - 就绪（源码 + tsx 依赖都在）→ 直接复用，跳过下载与安装
    - bb-sync 装的旧版本（元数据里的源码地址与当前不一致）→ 下载后原地更新
    - 残缺/损坏 → 备份为 ``.steel.broken-<时间戳>`` 后重装
    - 旧版 ``BB_SYNC_HOME/.steel`` → 自动迁移复用，随后清理旧目录；
      ``~/.steel`` 不可用时优先回退到它，两处都不可用才重新下载

    源码走 codeload zip（不依赖 git）；依赖安装在 ``.steel/api``
    （npm workspaces 自动提升到 ``.steel/node_modules``）。
    """
    if STEEL_ROOT.exists() and not is_deployed():
        _quarantine_broken_root(console)
    migrated = _migrate_legacy_root(console)
    if migrated and not is_deployed():
        # 旧目录本身也不可用：同样备份保留，再走全新下载
        _quarantine_broken_root(console)

    if is_deployed():
        if not _needs_upgrade():
            console.log(f"[steel] 复用已就绪的后端: {STEEL_ROOT}")
            if migrated and not _read_steel_meta():
                _write_steel_meta(console)  # 旧版安装补记归属，便于日后升级
            _cleanup_legacy_root(console)
            return
        console.log(f"[steel] 检测到 bb-sync 安装的旧版本，准备更新: {STEEL_ROOT}")

    _node_bin()  # 提前给出友好的 Node.js 缺失提示
    npm = _npm_bin()
    if not npm:
        raise EnvironmentError_("未找到 npm", "请安装 Node.js（自带 npm）后重试")

    STEEL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = STEEL_ROOT.parent / ".steel-tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    old_root: Path | None = None
    try:
        source = _download_source(console, tmp_dir)
        old_root = _install_source_tree(source)
        console.log(f"[steel] 源码已就位: {STEEL_ROOT}")
        _install_dependencies(npm, console)
    except BaseException:
        if old_root is not None:
            _rollback_old_root(old_root)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if old_root is not None:
        shutil.rmtree(old_root, ignore_errors=True)
    _write_steel_meta(console)
    _cleanup_legacy_root(console)
    console.success("Steel 后端部署完成")


def _find_tsx() -> Path | None:
    """tsx 可执行入口：npm workspaces 会把依赖提升到 ``~/.steel/node_modules``。"""
    return next(
        (
            d / "node_modules" / "tsx" / "dist" / "cli.mjs"
            for d in (STEEL_DIR, STEEL_DIR.parent)
            if (d / "node_modules" / "tsx" / "dist" / "cli.mjs").exists()
        ),
        None,
    )


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
    """Steel 服务端是否在跑。"""
    try:
        _http("GET", "/v1/sessions", timeout=3)
        return True
    except Exception:
        return False


def start_server(console: Console = _SILENT, headed: bool = False, wait: int = 120) -> None:
    """后台启动 Steel 服务端（独立进程，不阻塞；未部署时自动部署）。"""
    if not is_deployed():
        deploy(console)
    tsx = _find_tsx()
    if tsx is None:
        raise EnvironmentError_(
            f"Steel 依赖未就绪: {STEEL_LOG}",
            f"重新运行会自动重装，或手动删除 {STEEL_ROOT} 后重试",
        )

    env = dict(os.environ)
    env["CHROME_HEADLESS"] = "false" if headed else "true"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = STEEL_URL.rsplit(":", 1)[-1]
    if not env.get("CHROME_EXECUTABLE_PATH"):
        browser = detect_browser()
        if browser:
            env["CHROME_EXECUTABLE_PATH"] = browser
            if "edge" in Path(browser).name.lower():
                console.log(f"[steel] 未检测到 Chrome，回退使用 Edge: {browser}")
        else:
            console.warn("未检测到 Chrome/Edge，Steel 可能无法启动浏览器")

    STEEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    # 日志句柄需随后台进程长期存活，不适用 context manager
    logf = open(STEEL_LOG, "a", encoding="utf-8")  # noqa: SIM115
    flags = 0
    if IS_WIN:
        # CREATE_NO_WINDOW：控制台程序但不弹窗口（DETACHED_PROCESS 下子进程
        # 仍可能闪出控制台窗口）；POSIX 类型存根中不存在，用 getattr 兜底
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
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
    raise EnvironmentError_(
        f"Steel 服务端启动超时: {STEEL_LOG}",
        f"查看日志排查，或删除 {STEEL_ROOT} 后重试",
    )


def ensure_server(console: Console = _SILENT, headed: bool = False) -> None:
    """确保 Steel 服务端可用（已在跑则直接复用）。"""
    if healthy():
        console.log("[steel] 已在运行 ✓")
        return
    console.step("[steel] 启动服务端 ...")
    start_server(console, headed)
    console.success("Steel 服务端就绪")


def create_session(headed: bool = False, console: Console = _SILENT) -> dict:
    """创建（或复用）浏览器会话；persist + userDataDir 保证登录态留存。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "persist": True,
        "userDataDir": str(PROFILE_DIR),
        "blockAds": True,
        "dimensions": {"width": 1920, "height": 1080},
    }
    try:
        data = _http("POST", "/v1/sessions", payload, timeout=60)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise EnvironmentError_(f"创建 Steel 会话失败: {exc.code} {detail}") from exc
    mode = "有窗口模式" if headed else "静默无窗口"
    console.log(f"[steel] 会话已创建: {data.get('id')} ({mode})")
    return data


def release_session(session: dict, console: Console = _SILENT) -> None:
    """释放会话（登录态保留在 profile）。"""
    sid = session.get("id")
    if not sid:
        return
    # 会话可能已被服务端自动回收，不影响持久化 profile 里的登录态
    with contextlib.suppress(Exception):
        _http("DELETE", f"/v1/sessions/{sid}", timeout=15)
    console.log("[steel] 会话已释放（登录态保留在 profile）")


def connect(p, session: dict):
    """通过 CDP 接入 Steel 浏览器，返回 ``(browser, context, page)``。"""
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


__all__ = [
    "PROFILE_DIR",
    "STEEL_DIR",
    "STEEL_LOG",
    "STEEL_URL",
    "connect",
    "create_session",
    "deploy",
    "detect_browser",
    "ensure_server",
    "healthy",
    "is_deployed",
    "release_session",
    "start_server",
]
