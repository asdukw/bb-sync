"""附件下载：文件名推断、类型嗅探、增量去重。

服务器常返回 ``application/octet-stream``，且 BB 条目标题往往没有扩展名
（就叫 "Lec 01"），所以文件名按以下优先级推断：

1. 链接文本
2. ``Content-Disposition`` 里的 ``filename``
3. URL 末段

再依次用 ``Content-Type`` → **文件头嗅探** → URL 末段后缀 补全扩展名。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote

from bb_sync.blackboard.models import FileItem
from bb_sync.blackboard.scraper import sanitize_filename

#: 作业编号，用于归入 ``assignments/hwN/``
HW_NUM = re.compile(r"(?:hw|home\s*work|set|assignment)\D{0,3}(\d{1,2})", re.I)

#: 大于该体积的文件才参与「体积+后缀」去重
SIZE_DEDUP_THRESHOLD = 50_000

#: 视为「没给出真实扩展名」的后缀
_UNKNOWN_SUFFIXES = {".bin", ".exe", ".dat"}


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


def build_index(course_dir: Path) -> dict[str, Path]:
    """索引课程目录下已有文件，用于增量去重。

    - 键 1：``文件名（小写）`` → 路径
    - 键 2：``__size__<体积><后缀>`` → 路径（仅大文件，应对「同名不同内容/不同名同内容」）
    """
    index: dict[str, Path] = {}
    if not course_dir.exists():
        return index
    for path in course_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        index.setdefault(path.name.lower(), path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > SIZE_DEDUP_THRESHOLD:
            index.setdefault(f"__size__{size}{path.suffix.lower()}", path)
    return index


def resolve_name(item: FileItem, resp) -> tuple[str, bytes]:
    """确定最终文件名并返回 ``(文件名, 响应体)``。

    响应体在此处一次性读出，供后续类型嗅探与体积去重复用。
    """
    name = (item.name or "").strip()
    body = resp.body()

    if not name or name.lower() in {"download", "untitled", "link"}:
        name = _name_from_disposition(resp.headers) or sanitize_filename(
            unquote(item.url.split("?")[0].rsplit("/", 1)[-1]) or "file"
        )

    if not Path(name).suffix or Path(name).suffix.lower() in _UNKNOWN_SUFFIXES:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        guess = _guess_extension(ctype, body, item.url)
        if guess:
            base = name.rsplit(".", 1)[0] if Path(name).suffix else name
            name = base + guess

    return sanitize_filename(name), body


def download_item(
    ctx_request,
    item: FileItem,
    course_dir: Path,
    dry_run: bool,
    index: dict[str, Path] | None = None,
) -> tuple[str, str]:
    """下载单个条目，返回 ``(状态, 名称)``。

    状态取值：``downloaded`` / ``exists`` / ``would-download`` / ``empty``。
    """
    cat_dir = course_dir / item.category
    # 作业按 hwN 归档，对齐已有目录习惯（assignments/hw1/…）
    if item.category == "assignments" and item.name:
        m = HW_NUM.search(item.name)
        if m:
            cat_dir = cat_dir / f"hw{int(m.group(1))}"

    resp = ctx_request.get(item.url, timeout=60000)
    name, body = resolve_name(item, resp)

    # 与已有文件重名（不管在课程目录下哪一层）就跳过，避免重复下载
    already = (index or {}).get(name.lower())
    if not already and index and len(body) > SIZE_DEDUP_THRESHOLD:
        # 名称不同但实际是同一个文件（BB 标题 vs 你手工改过的名字）
        already = index.get(f"__size__{len(body)}{Path(name).suffix.lower()}")
    if already:
        try:
            rel = str(already.relative_to(course_dir)).replace("\\", "/")
        except ValueError:
            rel = already.name
        return ("exists", rel)

    cat_dir.mkdir(parents=True, exist_ok=True)
    target = cat_dir / name

    if dry_run:
        return ("would-download", target.name)
    if target.exists():
        return ("exists", target.name)
    if len(body) == 0:
        return ("empty", target.name)
    target.write_bytes(body)
    return ("downloaded", target.name)


__all__ = [
    "HW_NUM",
    "build_index",
    "download_item",
    "resolve_name",
    "sniff_ext",
]
