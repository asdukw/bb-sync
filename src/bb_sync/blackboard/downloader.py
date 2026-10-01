"""附件下载：文件名推断、类型嗅探、增量去重、原子落盘。

服务器常返回 ``application/octet-stream``，且 BB 条目标题往往没有扩展名
（就叫 "Lec 01"），所以文件名按以下优先级推断：

1. 链接文本
2. ``Content-Disposition`` 里的 ``filename``
3. URL 末段

再依次用 ``Content-Type`` → **文件头嗅探** → URL 末段后缀 补全扩展名。

下载走**流式 + 原子落盘**：先读文件头用于嗅探/去重，再分块写入 ``.part``，
成功后 ``os.replace`` 原子替换。失败/中断会保留 ``.part``，下次运行用
``Range`` 续传——避免「半截文件被当成已存在而永久跳过」。

网络层复用 Steel 会话的 cookie，不额外登录；流式通道整体失败时回退到
Playwright 请求上下文（旧的整块读取行为），可用性优先。
"""

from __future__ import annotations

import contextlib
import http.client
import mimetypes
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

from bb_sync.blackboard.models import BASE, FileItem
from bb_sync.blackboard.scraper import sanitize_filename

#: 作业编号，用于归入 ``assignments/hwN/``
HW_NUM = re.compile(r"(?:hw|home\s*work|set|assignment)\D{0,3}(\d{1,2})", re.I)

#: 大于该体积的文件才参与「体积+后缀」去重
SIZE_DEDUP_THRESHOLD = 50_000

#: 视为「没给出真实扩展名」的后缀
_UNKNOWN_SUFFIXES = {".bin", ".exe", ".dat"}

#: 未完成下载的临时文件后缀；``build_index`` 会忽略它
PART_SUFFIX = ".part"

#: 判断类型/去重所需的最少字节数（覆盖 ipynb 的 ``"cells"`` 探测窗口）
SNIFF_BYTES = 4096

#: 分块写入的块大小
CHUNK_SIZE = 1 << 20

#: 单次请求超时（秒）；流式按块计时，不受整文件大小影响
REQUEST_TIMEOUT = 60.0

#: 连接类故障的重试次数与退避基数
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0

#: 兜底 User-Agent（正常路径会尽量沿用浏览器上下文）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def sniff_ext(body: bytes) -> str | None:
    """按文件头嗅探真实类型（服务器常返回 application/octet-stream）。"""
    if body.startswith(b"%PDF"):
        return ".pdf"
    if body.startswith(b"PK\x03\x04"):
        return ".zip"
    if body.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    head = body[:400].lstrip()
    if head.startswith(b"{"):
        if b'"cells"' in body[:4000]:
            return ".ipynb"
        return ".json"
    if head.startswith(b"<?xml") or head.lower().startswith(b"<html"):
        return ".xml"
    if body.startswith(b"\x1f\x8b"):
        return ".gz"
    return None


def _name_from_disposition(headers) -> str | None:
    """从 ``Content-Disposition`` 提取文件名。"""
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    return unquote(m.group(1)) if m else None


def _guess_extension(ctype: str, body: bytes, url: str) -> str | None:
    """推断扩展名：Content-Type → 文件头 → URL 末段。"""
    guess = mimetypes.guess_extension(ctype) if ctype else None
    if guess in {None, ".bin", ".exe"}:
        guess = sniff_ext(body)
    if not guess:
        tail = unquote(url.split("?")[0].rsplit("/", 1)[-1])
        if "." in tail[-6:]:
            guess = "." + tail.rsplit(".", 1)[-1].lower()
    return guess


def _finalize_name(item: FileItem, headers, head: bytes, url: str) -> str:
    """由链接文本 / Content-Disposition / URL 推断并补全最终文件名。"""
    name = (item.name or "").strip()
    if not name or name.lower() in {"download", "untitled", "link"}:
        name = _name_from_disposition(headers) or sanitize_filename(
            unquote(url.split("?")[0].rsplit("/", 1)[-1]) or "file"
        )

    if not Path(name).suffix or Path(name).suffix.lower() in _UNKNOWN_SUFFIXES:
        ctype = str(headers.get("content-type", "")).split(";")[0].strip()
        guess = _guess_extension(ctype, head, url)
        if guess:
            base = name.rsplit(".", 1)[0] if Path(name).suffix else name
            name = base + guess

    return sanitize_filename(name)


def resolve_name(item: FileItem, resp) -> tuple[str, bytes]:
    """兼容旧签名（浏览器请求兜底路径）：读全量 body 后确定文件名。"""
    body = resp.body()
    return _finalize_name(item, resp.headers, body, item.url), body


def build_index(course_dir: Path) -> dict[str, Path]:
    """索引课程目录下已有文件，用于增量去重。

    - 键 1：``文件名（小写）`` → 路径
    - 键 2：``__size__<体积><后缀>`` → 路径（仅大文件，应对「同名不同内容/不同名同内容」）

    ``.part`` 等未完成的临时文件不参与索引，避免被误判成「已存在」。
    """
    index: dict[str, Path] = {}
    if not course_dir.exists():
        return index
    for path in course_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.name.endswith(PART_SUFFIX):
            continue
        index.setdefault(path.name.lower(), path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > SIZE_DEDUP_THRESHOLD:
            index.setdefault(f"__size__{size}{path.suffix.lower()}", path)
    return index


def _remember(index: dict[str, Path] | None, name: str, path: Path, size: int) -> None:
    """把本次新落盘的文件写回索引，供同一轮后续条目去重。"""
    if index is None:
        return
    index.setdefault(name.lower(), path)
    if size > SIZE_DEDUP_THRESHOLD:
        index.setdefault(f"__size__{size}{path.suffix.lower()}", path)


def _index_match(index: dict[str, Path] | None, name: str, size: int | None) -> Path | None:
    """按「文件名」或「体积+后缀」在索引里找已存在文件。"""
    if not index:
        return None
    found = index.get(name.lower())
    if found is not None:
        return found
    if size is not None and size > SIZE_DEDUP_THRESHOLD:
        return index.get(f"__size__{size}{Path(name).suffix.lower()}")
    return None


def _content_length(headers) -> int | None:
    """读取 ``Content-Length``；缺失或非法时返回 None。"""
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _expected_total(headers, status: int) -> int | None:
    """整段下载的预期总字节数：200 看 Content-Length，206 看 Content-Range。"""
    if status == 206:
        content_range = str(headers.get("content-range", ""))
        if "/" in content_range:
            tail = content_range.rsplit("/", 1)[-1].strip()
            if tail.isdigit():
                return int(tail)
        return None
    return _content_length(headers)


def _category_dir(course_dir: Path, item: FileItem) -> Path:
    """条目落盘目录；作业按 hwN 归档，对齐已有目录习惯。"""
    cat_dir = course_dir / item.category
    if item.category == "assignments" and item.name:
        m = HW_NUM.search(item.name)
        if m:
            cat_dir = cat_dir / f"hw{int(m.group(1))}"
    return cat_dir


def _relpath(path: Path, course_dir: Path) -> str:
    """相对课程目录的展示路径；跨盘等取不到相对路径时退回文件名。"""
    try:
        return str(path.relative_to(course_dir)).replace("\\", "/")
    except ValueError:
        return path.name


def _cookie_header(cookies, url: str) -> str:
    """只转发与目标域名匹配的 cookie，避免把登录态发给无关站点。"""
    host = (urlparse(url).hostname or "").lower()
    parts: list[str] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain and host and not (host == domain or host.endswith("." + domain)):
            continue
        name = cookie.get("name")
        if name:
            parts.append(f"{name}={cookie.get('value', '')}")
    return "; ".join(parts)


def _is_transient(exc: BaseException) -> bool:
    """连接超时/中断可重试；TLS 握手失败、4xx 等确定性失败不重试。

    ``urllib`` 会把底层 ``ssl.SSLError`` 包进 ``URLError.reason``，必须先拆一层，
    否则「握手失败」会被误当成瞬时错误，对每个文件白重试三次。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    reason = getattr(exc, "reason", None)
    if isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError):
        return False
    return isinstance(
        exc, (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError)
    )


class DownloadClient:
    """复用浏览器登录态的流式下载客户端。

    - 转发与目标域名匹配的 cookie，无需重新登录
    - ``open`` 返回可流式读取的响应对象；连接类故障按退避重试
    - ``fallback`` 是 Playwright 请求上下文，用作旧路径兜底
    """

    def __init__(
        self,
        *,
        cookies: list[dict] | tuple = (),
        fallback=None,
        retries: int = MAX_RETRIES,
        backoff: float = RETRY_BACKOFF,
        opener=None,
        user_agent: str = DEFAULT_USER_AGENT,
        on_retry=None,
        on_degraded=None,
    ) -> None:
        self._cookies = list(cookies)
        self._opener = opener
        self._user_agent = user_agent
        self._on_retry = on_retry
        self._on_degraded = on_degraded
        self.fallback = fallback
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        #: 流式通道失败一次后置位；本轮后续文件直接走浏览器兜底，不再反复失败/重试
        self.degraded = False

    def _request(self, url: str, offset: int) -> urllib.request.Request:
        request = urllib.request.Request(url)
        request.add_header("User-Agent", self._user_agent)
        request.add_header("Accept", "*/*")
        request.add_header("Referer", BASE + "/")
        cookie = _cookie_header(self._cookies, url)
        if cookie:
            request.add_header("Cookie", cookie)
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        return request

    def open(self, url: str, offset: int = 0):
        """发起请求并返回流式响应；仅对连接类故障退避重试。"""
        opener = self._opener or urllib.request.build_opener()
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                return opener.open(self._request(url, offset), timeout=REQUEST_TIMEOUT)
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries or not _is_transient(exc):
                    break
                self.notify(url, attempt, exc)
                time.sleep(self.backoff * (2**attempt))
        assert last_error is not None
        raise last_error

    def notify(self, url: str, attempt: int, exc: BaseException) -> None:
        """回调「第 attempt 次重试」，用于日志；回调异常不影响下载。"""
        if self._on_retry is None:
            return
        with contextlib.suppress(Exception):
            self._on_retry(url, attempt, exc)

    def mark_degraded(self, exc: BaseException) -> None:
        """流式通道失败后降级：本轮回退浏览器请求，避免每个文件重复失败。"""
        if self.degraded:
            return
        self.degraded = True
        if self._on_degraded is not None:
            with contextlib.suppress(Exception):
                self._on_degraded(exc)


def build_client(
    ctx,
    *,
    fallback=None,
    retries: int = MAX_RETRIES,
    backoff: float = RETRY_BACKOFF,
    on_retry=None,
    on_degraded=None,
) -> DownloadClient:
    """从浏览器上下文构造流式下载客户端（复用 cookie，避免重新登录）。"""
    try:
        cookies = ctx.cookies()
    except Exception:
        cookies = []
    return DownloadClient(
        cookies=cookies,
        fallback=fallback,
        retries=retries,
        backoff=backoff,
        on_retry=on_retry,
        on_degraded=on_degraded,
    )


def _pump(resp, part: Path, pending: bytes, mode: str) -> int:
    """把 ``pending`` 与响应剩余内容分块写入 ``part``，返回写入后的文件大小。"""
    with open(part, mode) as handle:
        if pending:
            handle.write(pending)
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)
    return part.stat().st_size


def _download_stream(client, url, target, *, first_resp, first_bytes) -> int:
    """把响应流写入 ``.part`` 后原子替换；失败保留 ``.part`` 供下次续传。"""
    part = target.with_name(target.name + PART_SUFFIX)
    part.parent.mkdir(parents=True, exist_ok=True)

    resp = first_resp
    pending = first_bytes
    # 已有未完成的 .part：探测响应只用于取名，这里改为带 Range 续传
    if part.exists() and part.stat().st_size > 0:
        with contextlib.suppress(Exception):
            resp.close()
        resp = None
        pending = b""

    last_error: BaseException | None = None

    for attempt in range(client.retries + 1):
        try:
            if resp is None:
                offset = part.stat().st_size if part.exists() else 0
                resp = client.open(url, offset=offset)
                status = int(getattr(resp, "status", 200))
                mode = "ab" if offset and status == 206 else "wb"
                pending = b""
            else:
                status = int(getattr(resp, "status", 200))
                mode = "wb"
            expected = _expected_total(getattr(resp, "headers", {}), status)
            written = _pump(resp, part, pending, mode)
            if expected is not None and written != expected:
                raise OSError(f"下载不完整：预期 {expected} 字节，实际 {written} 字节")
            with contextlib.suppress(Exception):
                resp.close()
            os.replace(part, target)
            return written
        except Exception as exc:
            last_error = exc
            with contextlib.suppress(Exception):
                if resp is not None:
                    resp.close()
            resp = None
            pending = b""
            if attempt >= client.retries or not _is_transient(exc):
                break
            client.notify(url, attempt, exc)
            time.sleep(client.backoff * (2**attempt))

    assert last_error is not None
    raise last_error


def _atomic_write(target: Path, data: bytes) -> None:
    """整块数据的原子写入（仅兜底路径使用）。"""
    part = target.with_name(target.name + PART_SUFFIX)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(data)
    os.replace(part, target)


def _download_streaming(
    client: DownloadClient,
    item: FileItem,
    course_dir: Path,
    dry_run: bool,
    index: dict[str, Path] | None,
) -> tuple[str, str]:
    """流式主路径：只读文件头即可判断名称/去重，dry-run 不落盘。"""
    cat_dir = _category_dir(course_dir, item)
    resp = client.open(item.url)
    try:
        head = resp.read(SNIFF_BYTES)
        headers = getattr(resp, "headers", {})
        name = _finalize_name(item, headers, head, item.url)
        size = _content_length(headers)
    except Exception:
        with contextlib.suppress(Exception):
            resp.close()
        raise

    already = _index_match(index, name, size)
    if already is not None:
        with contextlib.suppress(Exception):
            resp.close()
        return ("exists", _relpath(already, course_dir))

    target = cat_dir / name
    if target.exists():
        with contextlib.suppress(Exception):
            resp.close()
        return ("exists", target.name)

    if dry_run:
        with contextlib.suppress(Exception):
            resp.close()
        return ("would-download", name)

    if not head and (size is None or size == 0):
        with contextlib.suppress(Exception):
            resp.close()
        return ("empty", name)

    written = _download_stream(client, item.url, target, first_resp=resp, first_bytes=head)
    _remember(index, name, target, written)
    return ("downloaded", name)


def _download_via_browser(
    request_ctx,
    item: FileItem,
    course_dir: Path,
    dry_run: bool,
    index: dict[str, Path] | None,
) -> tuple[str, str]:
    """兜底路径：沿用 Playwright 请求上下文，整块读取后原子落盘。"""
    cat_dir = _category_dir(course_dir, item)
    resp = request_ctx.get(item.url, timeout=60000)
    name, body = resolve_name(item, resp)

    already = _index_match(index, name, len(body))
    if already is not None:
        return ("exists", _relpath(already, course_dir))

    target = cat_dir / name
    if target.exists():
        return ("exists", target.name)
    if dry_run:
        return ("would-download", name)
    if not body:
        return ("empty", name)

    _atomic_write(target, body)
    _remember(index, name, target, len(body))
    return ("downloaded", name)


def download_item(
    client: DownloadClient,
    item: FileItem,
    course_dir: Path,
    dry_run: bool,
    index: dict[str, Path] | None = None,
) -> tuple[str, str]:
    """下载单个条目，返回 ``(状态, 名称)``。

    状态取值：``downloaded`` / ``exists`` / ``would-download`` / ``empty``。
    流式通道整体失败时，若客户端带浏览器回退，则退回 Playwright 请求。
    """
    if client.degraded and client.fallback is not None:
        return _download_via_browser(client.fallback, item, course_dir, dry_run, index)
    try:
        return _download_streaming(client, item, course_dir, dry_run, index)
    except Exception as exc:
        if client.fallback is None:
            raise
        client.mark_degraded(exc)
        return _download_via_browser(client.fallback, item, course_dir, dry_run, index)


__all__ = [
    "CHUNK_SIZE",
    "DownloadClient",
    "HW_NUM",
    "MAX_RETRIES",
    "PART_SUFFIX",
    "SIZE_DEDUP_THRESHOLD",
    "SNIFF_BYTES",
    "build_client",
    "build_index",
    "download_item",
    "resolve_name",
    "sniff_ext",
]
