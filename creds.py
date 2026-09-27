"""凭据解析与存储：系统钥匙串（keyring）为主，.env 文件兜底。

- 钥匙串：Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service，
  凭据以 JSON 存在服务 "bb-sync" 下，不落明文文件。
- .env 兜底：CI 或无桌面环境等钥匙串不可用的场景。
- 优先级：钥匙串 > .env（若两者同时存在，提示删除 .env）。
"""

from __future__ import annotations

import json
from typing import NamedTuple

import keyring
import keyring.errors
from dotenv import dotenv_values

from paths import find_config_file

SERVICE = "bb-sync"
ACCOUNT = "credentials"


class Credentials(NamedTuple):
    student_id: str
    password: str
    source: str  # "keyring" / ".env"


def load_credentials() -> Credentials:
    """读取凭据：系统钥匙串优先，.env 文件兜底（旧键名兼容）。"""
    try:
        blob = keyring.get_password(SERVICE, ACCOUNT)
    except keyring.errors.KeyringError:
        blob = None
    if blob:
        data = json.loads(blob)
        return Credentials(data.get("student_id", ""), data.get("password", ""), "keyring")

    vals = dotenv_values(find_config_file(".env"))

    def get(*keys: str) -> str:
        for k in keys:
            val = vals.get(k)
            if val:
                return val
        return ""

    return Credentials(
        get("STUDENT_ID", "student_id", "username"),
        get("PASSWORD", "password"),
        ".env",
    )


def save_credentials(student_id: str, password: str) -> str:
    """凭据存入系统钥匙串，返回实际使用的后端名称。"""
    keyring.set_password(
        SERVICE,
        ACCOUNT,
        json.dumps(
            {
                "student_id": student_id,
                "password": password,
            }
        ),
    )
    return str(keyring.get_keyring())


def delete_credentials() -> bool:
    """从钥匙串删除凭据；不存在时返回 False。"""
    if keyring.get_password(SERVICE, ACCOUNT) is None:
        return False
    keyring.delete_password(SERVICE, ACCOUNT)
    return True


def env_file_exists() -> bool:
    """当前解析到的 .env 候选文件是否真实存在。"""
    return find_config_file(".env").exists()
