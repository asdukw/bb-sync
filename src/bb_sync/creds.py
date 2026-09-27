"""凭据存储：仅使用系统钥匙串（keyring），不在任何文件里落明文。

- 钥匙串：Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service，
  凭据以 JSON 存在服务 ``"bb-sync"`` 下，不落明文文件。
- 不再支持 ``.env`` 兜底：明文凭据容易误提交或泄漏，一律走钥匙串。
  若钥匙串在无桌面环境不可用，请改用交互式录入以外的部署方式。
"""

from __future__ import annotations

import json
from typing import NamedTuple

import keyring
import keyring.errors

SERVICE = "bb-sync"
ACCOUNT = "credentials"


class Credentials(NamedTuple):
    student_id: str
    password: str
    source: str  # 目前恒为 "keyring"

    @property
    def ok(self) -> bool:
        """两项都非空才算可用。"""
        return bool(self.student_id and self.password)


def load_credentials() -> Credentials:
    """从系统钥匙串读取凭据；不存在或读取失败时返回空凭据。"""
    try:
        blob = keyring.get_password(SERVICE, ACCOUNT)
    except keyring.errors.KeyringError:
        blob = None
    if not blob:
        return Credentials("", "", "keyring")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = {}
    return Credentials(data.get("student_id", ""), data.get("password", ""), "keyring")


def save_credentials(student_id: str, password: str) -> str:
    """凭据存入系统钥匙串，返回实际使用的后端名称。"""
    keyring.set_password(
        SERVICE,
        ACCOUNT,
        json.dumps({"student_id": student_id, "password": password}),
    )
    return str(keyring.get_keyring())


def delete_credentials() -> bool:
    """从钥匙串删除凭据；不存在时返回 False。"""
    if keyring.get_password(SERVICE, ACCOUNT) is None:
        return False
    keyring.delete_password(SERVICE, ACCOUNT)
    return True


def has_keyring_credentials() -> bool:
    """钥匙串里是否已有凭据（不读取明文内容）。"""
    try:
        return keyring.get_password(SERVICE, ACCOUNT) is not None
    except keyring.errors.KeyringError:
        return False


def mask(secret: str, tail: int = 4) -> str:
    """脱敏显示：只保留尾部若干位，其余用 * 替代。"""
    if not secret:
        return ""
    if len(secret) <= tail:
        return "*" * len(secret)
    return "*" * (len(secret) - tail) + secret[-tail:]


__all__ = [
    "ACCOUNT",
    "SERVICE",
    "Credentials",
    "delete_credentials",
    "has_keyring_credentials",
    "load_credentials",
    "mask",
    "save_credentials",
]
