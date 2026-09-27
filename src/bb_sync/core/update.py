"""检查 bb-sync 是否有新版本，并在落后时提示用户升级。

更新检查属于「锦上添花」的非关键路径，必须满足三条约束：

- **绝不阻塞/破坏主命令**：网络、缓存或 GitHub API 异常都静默忽略；
- **不频繁访问网络**：结果（含失败）缓存到 ``BB_SYNC_HOME/update-check.json``，
  默认 24 小时内不重复检查；
- **不污染 stdout**：提示通过 :class:`~bb_sync.core.output.Console` 写 stderr，
  ``--json`` 的 stdout 仍然只有命令结果。

只在普通命令成功返回后调用本模块；``--help`` / ``--version`` / 无参数等早退路径
不会触发网络请求。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib import error, request

from bb_sync import __version__, paths

if TYPE_CHECKING:  # 仅用于类型标注，运行时由 Console 自己惰性导入 rich
    from bb_sync.core.output import Console

#: GitHub 最新 release API。仓库是 bb-sync 的唯一发布入口。
RELEASES_API = "https://api.github.com/repos/asdukw/bb-sync/releases/latest"

#: 更新检查缓存文件（放在用户运行时目录，不写当前工作目录）。
CACHE_NAME = "update-check.json"

#: 同一版本检查结果的缓存时长：一天最多访问一次 GitHub。
CACHE_TTL_SECONDS = 24 * 60 * 60

#: 单次 HTTP 请求超时；更新检查不应明显拖慢 CLI 退出。
REQUEST_TIMEOUT_SECONDS = 2.0

#: 设置该环境变量为非空值时关闭自动更新检查（主要供脚本 / CI 使用）。
DISABLE_ENV = "BB_SYNC_NO_UPDATE_CHECK"

_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)")
_SOURCE_DEV_VERSION = "0.0.0.dev0"


@dataclass(frozen=True)
class ReleaseInfo:
    """GitHub 最新 release 的规范化信息。"""

    version: str
    tag: str
    url: str


@dataclass(frozen=True)
class UpdateInfo:
    """调用方需要展示的「当前版本 → 最新版本」信息。"""

    current: str
    latest: str
    release_url: str


def _version_key(value: str) -> tuple[int, ...] | None:
    """把 ``v1.2.3`` 这类版本号转成可比较的数字元组。

    只支持本项目使用的语义化数字版本；遇到无法识别的字符串时返回 ``None``，
    让调用方安静跳过，而不是猜一个可能错误的比较结果。
    """
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:  # pragma: no cover - 正则已限制为数字与点
        return None


def is_outdated(current: str, latest: str) -> bool:
    """判断 ``current`` 是否严格落后于 ``latest``。"""
    current_key = _version_key(current)
    latest_key = _version_key(latest)
    if current_key is None or latest_key is None:
        return False

    width = max(len(current_key), len(latest_key))
    current_padded = current_key + (0,) * (width - len(current_key))
    latest_padded = latest_key + (0,) * (width - len(latest_key))
    return current_padded < latest_padded


def _normalize_version(value: str) -> str | None:
    """去掉版本号开头的 ``v``，并校验其格式。"""
    normalized = value.strip()
    if normalized[:1] in {"v", "V"}:
        normalized = normalized[1:]
    return normalized if _version_key(normalized) is not None else None


def _release_url(version: str, tag: str | None = None) -> str:
    """GitHub release 页面地址；API 没给 html_url 时兜底生成。"""
    return f"https://github.com/asdukw/bb-sync/releases/tag/{tag or f'v{version}'}"


def _disabled() -> bool:
    """是否通过环境变量关闭更新检查。"""
    return bool(os.environ.get(DISABLE_ENV, "").strip())


def fetch_latest_release(timeout: float = REQUEST_TIMEOUT_SECONDS) -> ReleaseInfo | None:
    """请求 GitHub 最新 release；任何失败都返回 ``None``。"""
    req = request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"bb-sync/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, error.URLError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        return None
    version = _normalize_version(tag)
    if version is None:
        return None

    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url:
        html_url = _release_url(version, tag)
    return ReleaseInfo(version=version, tag=tag, url=html_url)


def _cache_path() -> Path:
    """缓存文件路径；每次调用都从 ``paths`` 读取，方便测试隔离。"""
    return paths.BB_SYNC_HOME / CACHE_NAME


def _read_cache() -> dict[str, Any] | None:
    """读取缓存；文件缺失、损坏或类型不对时都视为没有缓存。"""
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache(*, checked_at: float, release: ReleaseInfo | None) -> None:
    """原子写入缓存；写入失败不影响调用方。"""
    payload: dict[str, Any] = {"checked_at": checked_at}
    if release is None:
        payload["latest_version"] = None
    else:
        payload.update(
            {
                "latest_version": release.version,
                "tag_name": release.tag,
                "release_url": release.url,
            }
        )

    cache_path = _cache_path()
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError:
        # 用户目录只读等情况下仍允许主命令正常执行；下次再尝试检查。
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)


def _cache_release(payload: dict[str, Any]) -> ReleaseInfo | None:
    """从缓存 JSON 还原 release；非法字段按无缓存处理。"""
    version = payload.get("latest_version")
    if not isinstance(version, str):
        return None
    normalized = _normalize_version(version)
    if normalized is None:
        return None

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        tag = f"v{normalized}"
    url = payload.get("release_url")
    if not isinstance(url, str) or not url:
        url = _release_url(normalized, tag)
    return ReleaseInfo(version=normalized, tag=tag, url=url)


def _cache_checked_at(payload: dict[str, Any]) -> float | None:
    checked_at = payload.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return None
    return float(checked_at)


def check_for_update(
    *,
    current: str | None = None,
    now: float | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> UpdateInfo | None:
    """返回更新信息；没有新版本、已禁用或检查失败时返回 ``None``。

    ``current`` / ``now`` / ``timeout`` 主要供测试注入；生产调用使用默认值。
    """
    current_version = current or __version__
    # 源码直跑或开发版不提示更新：版本号本身不代表一个可升级的用户安装。
    if current_version == _SOURCE_DEV_VERSION or _disabled():
        return None

    checked_now = time.time() if now is None else now
    cache = _read_cache()
    if cache is not None:
        cached_at = _cache_checked_at(cache)
        cached_release = _cache_release(cache)
        if cached_at is not None and 0 <= checked_now - cached_at < CACHE_TTL_SECONDS:
            return _outdated_info(current_version, cached_release)
    else:
        cached_release = None

    release = fetch_latest_release(timeout=timeout)
    if release is None:
        # 网络失败也写缓存，避免离线用户每次执行命令都等待超时；若之前拿到过
        # 最新的版本号，则保留它，只更新检查时间（旧 release 不会倒退）。
        _write_cache(checked_at=checked_now, release=cached_release)
        return _outdated_info(current_version, cached_release)

    _write_cache(checked_at=checked_now, release=release)
    return _outdated_info(current_version, release)


def _outdated_info(current: str, release: ReleaseInfo | None) -> UpdateInfo | None:
    """把内部 release 转换为调用方需要展示的更新信息。"""
    if release is None or not is_outdated(current, release.version):
        return None
    return UpdateInfo(current=current, latest=release.version, release_url=release.url)


def notify_if_outdated(
    console: Console,
    *,
    current: str | None = None,
    now: float | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> UpdateInfo | None:
    """检查并输出更新提示；异常一律吞掉，绝不改变主命令结果。"""
    try:
        info = check_for_update(current=current, now=now, timeout=timeout)
    except Exception:  # 更新检查是附加功能，不能影响正常命令
        return None

    if info is None:
        return None
    console.warn(f"bb-sync 有新版本：当前 {info.current}，最新 {info.latest}")
    console.hint(f"运行 uv tool upgrade bb-sync 更新；详情见 {info.release_url}")
    return info


__all__ = [
    "CACHE_NAME",
    "CACHE_TTL_SECONDS",
    "DISABLE_ENV",
    "RELEASES_API",
    "REQUEST_TIMEOUT_SECONDS",
    "ReleaseInfo",
    "UpdateInfo",
    "check_for_update",
    "fetch_latest_release",
    "is_outdated",
    "notify_if_outdated",
]
