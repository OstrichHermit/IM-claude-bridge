"""
外部消息注入 API 测试

全部使用临时目录的 DB（monkeypatch external_api.get_db_path），
绝不读写真实 shared/messages.db。
"""
import os
import secrets
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from shared.config import Config
from shared.message_queue import MessageQueue
from web import external_api
from web import web_server

TEST_TOKEN = "test-token-" + secrets.token_hex(8)
CHANNEL_ID = 1477362651859255326  # 与 hs watch 约定一致（字符串形式的 Discord 雪花 ID）
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
URL = "/api/external/message"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """带临时 DB 和测试 token 的 TestClient"""
    db_path = tmp_path / "messages.db"

    # DB 指到临时目录（最不侵入：只替换 external_api.get_db_path）
    monkeypatch.setattr(external_api, "get_db_path", lambda: str(db_path))
    # token / allowed_sources 用测试值（不读真实 config.yaml）
    monkeypatch.setattr(Config, "external_api_token", property(lambda self: TEST_TOKEN))
    monkeypatch.setattr(Config, "external_api_allowed_sources", property(lambda self: ["hs-watch"]))

    return TestClient(web_server.app), db_path


def _fetch_row(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        # messages 表可能尚未创建（如请求在注入前就被拦截）
        exists = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if not exists:
            return []
        cursor.execute("""
            SELECT id, direction, content, status, discord_channel_id,
                   discord_user_id, username, is_dm, is_external, tag, channel_type
            FROM messages
        """)
        return cursor.fetchall()
    finally:
        conn.close()


# ========== 401 鉴权失败 ==========

def test_401_no_auth_header(client):
    c, _ = client
    resp = c.post(URL, json={"channel_id": str(CHANNEL_ID), "content": "hi", "source": "hs-watch"})
    assert resp.status_code == 401
    assert resp.json() == {"success": False, "error": "unauthorized"}


def test_401_wrong_token(client):
    c, _ = client
    resp = c.post(
        URL,
        headers={"Authorization": "Bearer wrong-token"},
        json={"channel_id": str(CHANNEL_ID), "content": "hi", "source": "hs-watch"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"success": False, "error": "unauthorized"}


def test_401_token_not_configured(client, monkeypatch):
    """token 未配置（空）时即使请求头格式正确也拒绝"""
    c, _ = client
    monkeypatch.setattr(Config, "external_api_token", property(lambda self: ""))
    resp = c.post(URL, headers=AUTH, json={"channel_id": str(CHANNEL_ID), "content": "hi", "source": "hs-watch"})
    assert resp.status_code == 401
    assert resp.json() == {"success": False, "error": "unauthorized"}


def test_401_takes_precedence_over_bad_body(client):
    """鉴权先于 body 解析：无鉴权 + 坏 body 也返回 401"""
    c, _ = client
    resp = c.post(URL, content="not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 401


# ========== 400 参数错误 ==========

def test_400_invalid_json(client):
    c, _ = client
    resp = c.post(URL, headers={**AUTH, "Content-Type": "application/json"}, content="not json")
    assert resp.status_code == 400
    assert resp.json()["success"] is False
    assert "error" in resp.json()


def test_400_non_dict_body(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json=[1, 2, 3])
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_400_missing_channel_id(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json={"content": "hi", "source": "hs-watch"})
    assert resp.status_code == 400
    assert "channel_id" in resp.json()["error"]


def test_400_empty_content(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json={"channel_id": str(CHANNEL_ID), "content": "", "source": "hs-watch"})
    assert resp.status_code == 400
    assert "content" in resp.json()["error"]


def test_400_missing_content(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json={"channel_id": str(CHANNEL_ID), "source": "hs-watch"})
    assert resp.status_code == 400
    assert "content" in resp.json()["error"]


def test_400_non_numeric_channel_id(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json={"channel_id": "abc", "content": "hi", "source": "hs-watch"})
    assert resp.status_code == 400
    assert "channel_id" in resp.json()["error"]


# ========== allowed_sources 逻辑 ==========

def test_400_source_not_allowed(client):
    """不在 allowed_sources 中的 source 被拦截（400），防止 session 被路由到临时会话"""
    c, db_path = client
    resp = c.post(URL, headers=AUTH, json={"channel_id": str(CHANNEL_ID), "content": "hi", "source": "rogue"})
    assert resp.status_code == 400
    assert resp.json()["success"] is False
    # 拦截时不入库
    assert _fetch_row(db_path) == []


def test_400_missing_source_when_whitelist_configured(client):
    c, _ = client
    resp = c.post(URL, headers=AUTH, json={"channel_id": str(CHANNEL_ID), "content": "hi"})
    assert resp.status_code == 400
    assert "source" in resp.json()["error"]


# ========== 200 成功注入 ==========

def test_200_inject_success_and_db_fields(client):
    """正确入库：断言 messages 表各字段值 + session_key 路由"""
    c, db_path = client
    body = {"channel_id": str(CHANNEL_ID), "content": "test-external-你好", "source": "hs-watch"}
    resp = c.post(URL, headers=AUTH, json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["session_key"] == f"channel_{CHANNEL_ID}"
    message_id = data["message_id"]
    assert isinstance(message_id, int)

    rows = _fetch_row(db_path)
    assert len(rows) == 1
    (row_id, direction, content, status, discord_channel_id,
     discord_user_id, username, is_dm, is_external, tag, channel_type) = rows[0]

    assert row_id == message_id
    assert direction == "to_claude"
    assert content == "test-external-你好"
    assert status == "pending"
    assert discord_channel_id == CHANNEL_ID
    assert is_dm in (0, False)
    assert is_external in (1, True)
    assert tag == "hs-watch"
    assert channel_type == "discord"

    # 用真实路由逻辑核对 session_key（必须落到 channel_{channel_id}，而非 temp_/dm_）
    queue = MessageQueue(str(db_path))
    msg = queue.get_message_by_id(message_id)
    assert queue._calculate_session_key(msg) == f"channel_{CHANNEL_ID}"


# ========== 回归：现有路由不受影响 ==========

def test_get_status_regression(client):
    c, _ = client
    resp = c.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data and "timestamp" in data
    assert "claude_bridge" in data["status"]
