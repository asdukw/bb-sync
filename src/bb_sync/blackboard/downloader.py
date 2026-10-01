"""附件下载：文件名推断、类型嗅探、增量去重、浏览器原生流式落盘。

服务器常返回 ``application/octet-stream``，且 BB 条目标题往往没有扩展名
（就叫 "Lec 01"），所以文件名按以下优先级推断：

1. 链接文本
2. ``Content-Disposition`` 里的 ``filename``
3. URL 末段

再依次用 ``Content-Type`` → **文件头嗅探** → URL 末段后缀 补全扩展名。

下载分两步走：

- **探测**：``HEAD`` 廉价拿文件名与大小，先做增量去重；名字缺扩展名时才补读文件头。
- **下载**：确实需要新文件时，用页面里的 ``<a download>`` 触发 Chrome 自身下载，
  由浏览器把文件**直接流式写到磁盘**（不再把整个文件读进内存）。落盘统一先写
  ``.part`` 再 ``os.replace`` 原子替换。

选择浏览器通道而不是自建 HTTP 客户端的原因：下载与登录共用同一套浏览器网络栈和
cookie，不会出现「浏览器能下、脚本直连被 TLS/代理拒绝」这类不一致（实测踩到过）。
"""

from __future__ import annotations

import contextlib
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import unquote

from bb_sync.blackboard.models import FileItem
from bb_sync.blackboard.scraper import sanitize_filename
from bb_sync.core.errors import NetworkError

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

#: HEAD 探测 / 嗅探文件头的超时（毫秒）
PROBE_TIMEOUT_MS = 30_000

#: 等待浏览器「开始下载」的超时（毫秒）；超时即认为浏览器通道不可用并熔断
DOWNLOAD_START_TIMEOUT_MS = 15_000

#: 兜底路线整块读取的超时（毫秒）
BUFFERED_TIMEOUT_MS = 60_000

#: 在页面里触发浏览器下载：同源 URL + download 属性，无论服务端是否 attachment 都会下载
_TRIGGER_DOWNLOAD_JS = """
(url) => {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}
"""


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
    cd = str(headers.get("content-disposition", "") or "")
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
        ctype = str(headers.get("content-type", "") or "").split(";")[0].strip()
        guess = _guess_extension(ctype, head, url)
        if guess:
            base = name.rsplit(".", 1)[0] if Path(name).suffix else name
            name = base + guess

    return sanitize_filename(name)


def _name_needs_sniff(name: str) -> bool:
    """文件名是否还缺一个可信扩展名（需要读文件头来嗅探）。"""
    suffix = Path(name).suffix.lower()
    return not suffix or suffix in _UNKNOWN_SUFFIXES


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


class DownloadSession:
    """一次同步复用的下载器（浏览器原生通道 + 整块读取兜底）。

    - ``probe`` 只用 HEAD 拿文件名/大小，命中已有文件时**完全不下载体**
    - 需要新下载时优先用浏览器原生下载，由 Chrome 直接流式写盘
    - 浏览器下载在本轮首次失败后熔断，后续文件直接走整块读取，不再重复等待超时
    """

    def __init__(self, page, request_ctx) -> None:
        self.page = page
        self.request = request_ctx
        self.browser_download_failed = False

    def probe(self, item: FileItem) -> tuple[str, int | None]:
        """探测文件名与大小：HEAD 优先，名字缺扩展名时才补读文件头。"""
        headers: dict = {}
        try:
            resp = self.request.fetch(item.url, method="HEAD", timeout=PROBE_TIMEOUT_MS)
            if resp.status < 400:
                headers = dict(resp.headers)
        except Exception:
            headers = {}

        head = b""
        name = _finalize_name(item, headers, head, item.url)
        if _name_needs_sniff(name):
            head = self._peek_head(item.url)
            if head:
                name = _finalize_name(item, headers, head, item.url)
        return name, _content_length(headers)

    def _peek_head(self, url: str) -> bytes:
        """读取文件头用于嗅探；服务端支持 Range 时只取几 KB。"""
        try:
            resp = self.request.get(
                url, headers={"Range": f"bytes=0-{SNIFF_BYTES - 1}"}, timeout=PROBE_TIMEOUT_MS
            )
            return resp.body()[:SNIFF_BYTES]
        except Exception:
            return b""

    def download_item(
        self,
        item: FileItem,
        course_dir: Path,
        dry_run: bool,
        index: dict[str, Path] | None = None,
    ) -> tuple[str, str]:
        """下载单个条目，返回 ``(状态, 名称)``。

        状态取值：``downloaded`` / ``exists`` / ``would-download`` / ``empty``。
        """
        cat_dir = _category_dir(course_dir, item)
        name, size = self.probe(item)

        already = _index_match(index, name, size)
        if already is not None:
            return ("exists", _relpath(already, course_dir))
        target = cat_dir / name
        if target.exists():
            return ("exists", target.name)
        if dry_run:
            return ("would-download", name)
        if size == 0:
            return ("empty", name)

        part = target.with_name(target.name + PART_SUFFIX)
        part.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            part.unlink()

        if not self._fetch_body(item.url, part):
            raise NetworkError(f"下载失败：{name}", "检查网络或登录态后重试 bb-sync run")

        written = part.stat().st_size
        if written == 0:
            with contextlib.suppress(OSError):
                part.unlink()
            return ("empty", name)
        if size is not None and written != size:
            with contextlib.suppress(OSError):
                part.unlink()
            raise NetworkError(
                f"下载不完整：{name}（预期 {size} 字节，实际 {written} 字节）",
                "文件未落盘，重试 bb-sync run 即可",
            )

        os.replace(part, target)
        _remember(index, name, target, written)
        return ("downloaded", name)

    def _fetch_body(self, url: str, part: Path) -> bool:
        """取回文件体：优先浏览器原生下载，失败后本轮改用整块读取。"""
        if not self.browser_download_failed:
            if self._try_browser_download(url, part):
                return True
            self.browser_download_failed = True
        return self._try_buffered(url, part)

    def _try_browser_download(self, url: str, part: Path) -> bool:
        """用页面里的 ``<a download>`` 触发浏览器下载，由 Chrome 流式写盘。"""
        try:
            with self.page.expect_download(timeout=DOWNLOAD_START_TIMEOUT_MS) as info:
                self.page.evaluate(_TRIGGER_DOWNLOAD_JS, url)
            download = info.value
            download.save_as(str(part))
            if download.failure() or not part.exists():
                raise OSError("浏览器下载未完成")
            return True
        except Exception:
            with contextlib.suppress(OSError):
                part.unlink()
            return False

    def _try_buffered(self, url: str, part: Path) -> bool:
        """兜底：用请求上下文整块读取后写盘。"""
        try:
            resp = self.request.get(url, timeout=BUFFERED_TIMEOUT_MS)
            part.write_bytes(resp.body())
        except Exception:
            return False
        return True


__all__ = [
    "BUFFERED_TIMEOUT_MS",
    "DOWNLOAD_START_TIMEOUT_MS",
    "DownloadSession",
    "HW_NUM",
    "PART_SUFFIX",
    "PROBE_TIMEOUT_MS",
    "SIZE_DEDUP_THRESHOLD",
    "SNIFF_BYTES",
    "build_index",
    "sniff_ext",
]
