"""
上下文用量监控测试（shared/context_usage.py + 配置开关 + 数据库读写 + 提示词注入 + 两端展示）

全部使用 tmp_path 临时库 / 临时配置，绝不读写真实 shared/messages.db 和真实 config.yaml；
模块级 logger 与 SessionWorker 的日志一律换成 Recorder，不往线上日志文件里写。
端到端用例沿用 test_process_utils 的做法：真实的 SessionWorker._call_claude_cli + 假 claude 脚本进程。
"""
import asyncio
import json
import sqlite3
import sys
import types
from pathlib import Path

import discord
import pytest
import yaml

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from bridge import session_worker
from shared.config import Config
from shared.context_usage import (
    DEFAULT_CONTEXT_WINDOW,
    build_context_bar,
    context_percentage,
    format_context_usage,
    format_usage_text,
    format_window_tokens,
    usage_to_context_tokens,
)
from shared.message_queue import Message, MessageDirection, MessageQueue, MessageStatus, MessageTag

SESSION_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = 4321
USERNAME = "测试用户"
PROMPT = "帮我看看这个文件"

CONTEXT_LINE_12 = "▓░░░░░░░░░ 上下文 已用 12% / 1M Tokens"
QUOTA_ENTRY = "▓░░░░░░░░░ 5小时 已用 12% / 08:00 重置"


class _Recorder:
    """记录日志行的假 logger（不往线上日志文件里写）"""

    def __init__(self):
        self.lines = []

    def log(self, message):
        self.lines.append(message)


# ========== usage_to_context_tokens ==========

def test_usage_to_context_tokens_sums_three_input_fields():
    """上下文已用 = input + cache_creation + cache_read，output_tokens 不计入"""
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 2000,
        "cache_read_input_tokens": 3000,
        "output_tokens": 500,
    }
    assert usage_to_context_tokens(usage) == 6000


def test_usage_to_context_tokens_ignores_output_only():
    """只有 output_tokens 时算 0"""
    assert usage_to_context_tokens({"output_tokens": 500}) == 0


def test_usage_to_context_tokens_partial_fields():
    """部分字段缺失时其余字段照常求和"""
    assert usage_to_context_tokens({"input_tokens": 100, "cache_read_input_tokens": 20}) == 120
    assert usage_to_context_tokens({"cache_creation_input_tokens": 50}) == 50


@pytest.mark.parametrize("usage", [
    None,
    "not a dict",
    123,
    [],
    {},
    {"input_tokens": None, "cache_creation_input_tokens": None, "cache_read_input_tokens": None},
    {"input_tokens": "123", "cache_creation_input_tokens": [], "cache_read_input_tokens": {}},
    {"input_tokens": True},
])
def test_usage_to_context_tokens_tolerates_bad_input(usage):
    """非 dict / 字段缺失 / None / 非数字 都按 0 算，不抛异常"""
    assert usage_to_context_tokens(usage) == 0


# ========== format_window_tokens / format_usage_text / context_percentage / 进度条 ==========

@pytest.mark.parametrize("n, expected", [
    (1_000_000, "1M"),
    (2_000_000, "2M"),
    (1_500_000, "1.5M"),
    (500_000, "500K"),
    (200_000, "200K"),
    (128_000, "128K"),
    (10_000, "10K"),
    (1_000, "1K"),
    (999, "999"),
    (0, "0"),
])
def test_format_window_tokens(n, expected):
    """百万以上用 M、千以上用 K，整数不带小数"""
    assert format_window_tokens(n) == expected


def test_format_usage_text():
    """用量文字只有百分比和窗口：已用 12% / 1M Tokens"""
    assert format_usage_text(118_729, 1_000_000) == "已用 12% / 1M Tokens"
    assert format_usage_text(50_000, 200_000) == "已用 25% / 200K Tokens"


def test_context_percentage_rounds():
    assert context_percentage(0, 1_000_000) == 0
    assert context_percentage(118_729, 1_000_000) == 12
    assert context_percentage(950_000, 1_000_000) == 95
    assert context_percentage(1_000_000, 1_000_000) == 100
    assert context_percentage(1_234, 10_000) == 12
    assert context_percentage(100_000, 200_000) == 50  # 窗口可配置


def test_context_percentage_caps_at_100():
    """超过窗口时封顶 100"""
    assert context_percentage(5_000_000, 1_000_000) == 100


def test_context_percentage_not_negative():
    assert context_percentage(-5, 1_000_000) == 0


@pytest.mark.parametrize("bad_window", [0, -1, 0.0, None, "", "abc", [1]])
def test_context_percentage_falls_back_to_default_window(bad_window):
    """窗口为非正数 / 非法值时按 DEFAULT_CONTEXT_WINDOW 算，不抛异常"""
    assert context_percentage(120_000, bad_window) == 12


def test_build_context_bar_zero_is_all_empty():
    bar, text = build_context_bar(0, 1_000_000)
    assert bar == "░" * 10
    assert text == "上下文 已用 0% / 1M Tokens"


def test_build_context_bar_12_percent_is_one_cell():
    bar, text = build_context_bar(118_729, 1_000_000)
    assert bar == "▓░░░░░░░░░"
    assert text == "上下文 已用 12% / 1M Tokens"


@pytest.mark.parametrize("used", [950_000, 990_000, 1_000_000, 5_000_000])
def test_build_context_bar_95_and_above_is_full(used):
    bar, _ = build_context_bar(used, 1_000_000)
    assert bar == "▓" * 10


def test_build_context_bar_matches_quota_bar_algorithm():
    """进度条算法与智谱额度条目完全一致（10 格，四舍五入到整格），逐个百分比比对"""
    for percentage in range(101):
        quota_bar = "▓" * int(percentage / 10 + 0.5)
        quota_bar = quota_bar + "░" * max(0, 10 - len(quota_bar))
        bar, text = build_context_bar(percentage * 10_000, 1_000_000)
        assert bar == quota_bar, f"percentage={percentage}"
        assert text.startswith(f"上下文 已用 {percentage}% ")


def test_build_context_bar_falls_back_when_window_invalid():
    bar, text = build_context_bar(118_729, 0)
    assert bar == "▓░░░░░░░░░"
    assert text == "上下文 已用 12% / 1M Tokens"


def test_format_context_usage_full_string():
    assert format_context_usage(118_729, 1_000_000) == CONTEXT_LINE_12


# ========== 配置开关 ==========

def _config_with(tmp_path, values: dict) -> Config:
    """用临时 yaml 构造 Config（不读真实 config.yaml）"""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
    return Config(str(path))


def test_config_defaults(tmp_path):
    config = _config_with(tmp_path, {})
    assert config.context_monitor_enabled is True
    assert config.context_prompt_enabled is True
    assert DEFAULT_CONTEXT_WINDOW == 1_000_000
    assert config.context_window_tokens == DEFAULT_CONTEXT_WINDOW


def test_config_reads_yaml_keys(tmp_path):
    config = _config_with(tmp_path, {
        "context_monitor_enabled": False,
        "context_prompt_enabled": False,
        "context_window_tokens": 200_000,
    })
    assert config.context_monitor_enabled is False
    assert config.context_prompt_enabled is False
    assert config.context_window_tokens == 200_000


def test_config_window_accepts_numeric_string(tmp_path):
    config = _config_with(tmp_path, {"context_window_tokens": "200000"})
    assert config.context_window_tokens == 200_000


@pytest.mark.parametrize("bad", [0, -5, "abc", "", None, [1]])
def test_config_window_falls_back_on_invalid_value(tmp_path, bad):
    config = _config_with(tmp_path, {"context_window_tokens": bad})
    assert config.context_window_tokens == DEFAULT_CONTEXT_WINDOW


# ========== 数据库读写 ==========

@pytest.fixture()
def queue(tmp_path):
    """临时库上的 MessageQueue（构造时自动跑迁移）"""
    return MessageQueue(str(tmp_path / "messages.db"))


def _make_session(queue, working_dir):
    """拿一个私聊会话的 session_key"""
    session_key, _, _, _ = queue.get_or_create_session(
        working_dir, channel_id=None, user_id=USER_ID, is_dm=True
    )
    return session_key


def _add_message(queue, content="你好") -> int:
    """往队列里塞一条私聊消息，返回 message_id"""
    message = Message(
        id=None,
        direction=MessageDirection.TO_CLAUDE.value,
        content=content,
        status=MessageStatus.PENDING.value,
        discord_channel_id=0,
        discord_message_id=0,
        discord_user_id=USER_ID,
        username=USERNAME,
        is_dm=True,
    )
    return queue.add_message(message)


def test_migration_adds_context_usage_columns(tmp_path):
    """新库自动迁移到 version 14，sessions / messages 表都有 context_usage 列"""
    db_path = tmp_path / "messages.db"
    MessageQueue(str(db_path))  # 构造即迁移
    conn = sqlite3.connect(str(db_path))
    try:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        sessions_cols = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
        messages_cols = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    finally:
        conn.close()
    assert version >= 14
    assert "context_usage" in sessions_cols
    assert "context_usage" in messages_cols


def test_save_and_read_context_usage(tmp_path, queue):
    """save_context_usage 后会话级 / 消息级都能读回；再次保存是覆盖"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)

    assert queue.sessions.get_context_usage(session_key) is None
    assert queue.get_message_context_usage(message_id) is None

    queue.save_context_usage(session_key, message_id, 6000)
    assert queue.sessions.get_context_usage(session_key) == 6000
    assert queue.get_message_context_usage(message_id) == 6000

    queue.save_context_usage(session_key, message_id, 118729)
    assert queue.sessions.get_context_usage(session_key) == 118729
    assert queue.get_message_context_usage(message_id) == 118729


def test_update_context_usage_does_not_touch_last_used_at(tmp_path, queue):
    """记录用量不改 last_used_at（它只由 get_or_create_session 维护）"""
    session_key = _make_session(queue, str(tmp_path))
    frozen = "2000-01-01T00:00:00"
    conn = sqlite3.connect(queue.db_path)
    try:
        conn.execute("UPDATE sessions SET last_used_at = ? WHERE session_key = ?", (frozen, session_key))
        conn.commit()
    finally:
        conn.close()

    assert queue.sessions.update_context_usage(session_key, 4321) is True

    conn = sqlite3.connect(queue.db_path)
    try:
        row = conn.execute(
            "SELECT context_usage, last_used_at FROM sessions WHERE session_key = ?", (session_key,)
        ).fetchone()
    finally:
        conn.close()
    assert row == (4321, frozen)


def test_save_context_usage_ignores_non_positive_tokens(tmp_path, queue):
    """tokens <= 0 直接返回，不写库也不抛异常"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)

    for tokens in (0, -100, None):
        queue.save_context_usage(session_key, message_id, tokens)

    assert queue.sessions.get_context_usage(session_key) is None
    assert queue.get_message_context_usage(message_id) is None


def test_save_context_usage_tolerates_missing_rows(tmp_path, queue):
    """message_id 不存在 / session 在 sessions 表里没有记录（如临时会话）时都不抛异常"""
    session_key = _make_session(queue, str(tmp_path))

    queue.save_context_usage(session_key, 999999, 100)  # 消息不存在：会话级照常更新
    assert queue.sessions.get_context_usage(session_key) == 100
    assert queue.get_message_context_usage(999999) is None

    queue.save_context_usage("temp_123", None, 100)  # 临时会话没有记录：静默忽略
    assert queue.sessions.get_context_usage("temp_123") is None
    assert queue.sessions.update_context_usage("temp_123", 100) is False

    assert queue.get_message_context_usage(None) is None


def test_session_context_usage_logs_and_returns_false_on_error(queue, monkeypatch):
    """session_manager 读写出错：写日志、返回 False / None，不抛异常"""
    from shared import session_manager

    recorder = _Recorder()
    monkeypatch.setattr(session_manager, "log", recorder)

    def boom():
        raise RuntimeError("db boom")

    monkeypatch.setattr(queue.sessions, "_get_connection", boom)

    assert queue.sessions.update_context_usage("dm_1", 100) is False
    assert queue.sessions.get_context_usage("dm_1") is None
    assert any("更新上下文用量失败" in line and "db boom" in line for line in recorder.lines)
    assert any("读取上下文用量失败" in line for line in recorder.lines)


def test_save_context_usage_never_raises(tmp_path, queue, monkeypatch):
    """message_queue 层读写出错：只打 ⚠️ 日志，绝不抛异常"""
    from shared import message_queue as message_queue_module

    recorder = _Recorder()
    monkeypatch.setattr(message_queue_module, "log", recorder)
    monkeypatch.setattr(queue, "db_path", str(tmp_path / "no_such_dir" / "messages.db"))

    queue.save_context_usage(None, 1, 100)  # 库打不开
    queue.save_context_usage(None, 1, "abc")  # tokens 非数字
    assert queue.get_message_context_usage(1) is None

    assert sum("保存上下文用量失败" in line for line in recorder.lines) == 2
    assert any("读取消息上下文用量失败" in line for line in recorder.lines)


# ========== 提示词注入：_build_context_line ==========

def _make_worker(queue, tmp_path, **overrides) -> session_worker.SessionWorker:
    """构造 SessionWorker：配置用 SimpleNamespace（只给本功能用得到的字段），日志换成 Recorder"""
    values = {
        "context_prompt_enabled": True,
        "context_window_tokens": DEFAULT_CONTEXT_WINDOW,
        "stickers_path": str(tmp_path / "stickers"),
        "max_attempts": 1,
        "claude_timeout": 30,
        "claude_executable": sys.executable,
        "working_directory": str(tmp_path),
        "enable_message_splitting": False,
        "weixin_message_splitting_enabled": False,
    }
    values.update(overrides)
    worker = session_worker.SessionWorker(f"dm_{USER_ID}", types.SimpleNamespace(**values), queue)
    worker._log = _Recorder()
    return worker


def test_build_context_line_with_data(tmp_path, queue):
    session_key = _make_session(queue, str(tmp_path))
    queue.sessions.update_context_usage(session_key, 118729)

    line = _make_worker(queue, tmp_path)._build_context_line(session_key)

    assert line.startswith("[上下文用量]")
    assert line == "[上下文用量] 已用 12% / 1M Tokens"


def test_build_context_line_uses_configured_window(tmp_path, queue):
    session_key = _make_session(queue, str(tmp_path))
    queue.sessions.update_context_usage(session_key, 50_000)

    line = _make_worker(queue, tmp_path, context_window_tokens=200_000)._build_context_line(session_key)

    assert line == "[上下文用量] 已用 25% / 200K Tokens"


def test_build_context_line_without_data(tmp_path, queue):
    """新会话（还没统计过）/ 会话不存在 / 没有 session_key：都返回 None"""
    worker = _make_worker(queue, tmp_path)
    session_key = _make_session(queue, str(tmp_path))

    assert worker._build_context_line(session_key) is None
    assert worker._build_context_line("dm_999999") is None
    assert worker._build_context_line(None) is None
    assert worker._build_context_line("") is None


def test_build_context_line_never_raises(tmp_path):
    """取用量过程出错（这里是 message_queue 没有 sessions）：返回 None 并记日志，不影响正常调用"""
    worker = _make_worker(types.SimpleNamespace(), tmp_path)

    assert worker._build_context_line("dm_1") is None
    assert any("读取上下文用量失败" in line for line in worker._log.lines)


# ========== 端到端：真实 _call_claude_cli + 假 claude 脚本 ==========

# 假 claude 脚本：argv[1] 事件文件（每行一个 stream-json 事件）原样写到 stdout；
# argv[2] 退出码；argv[3] 输出完事件后先挂起多少秒（模拟还没跑完就被打断）
FAKE_CLAUDE_SCRIPT = (
    "import sys, time\n"
    "events_path, exit_code, hang = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "with open(events_path, 'r', encoding='utf-8') as fh:\n"
    "    sys.stdout.write(fh.read())\n"
    "sys.stdout.flush()\n"
    "if hang > 0:\n"
    "    time.sleep(hang)\n"
    "sys.exit(exit_code)\n"
)

# input 100 + cache_creation 50 + cache_read 20 = 170（output 不计入）
USAGE_SMALL = {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 20, "output_tokens": 10}
# input 1000 + cache_creation 2000 + cache_read 3000 = 6000
USAGE_LARGE = {"input_tokens": 1000, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 3000, "output_tokens": 500}
# 真实流里 assistant 事件自带的 usage 就是这个样子：全是 0（真实数字在同一次调用收尾的 message_delta 里）
USAGE_ZERO = {"input_tokens": 0, "output_tokens": 0}


def _init_event():
    return {"type": "system", "subtype": "init", "session_id": SESSION_ID}


def _assistant_event(message_id, content, usage):
    return {
        "type": "assistant",
        "message": {"id": message_id, "type": "message", "role": "assistant", "content": content, "usage": usage},
    }


def _delta_event(usage, parent_tool_use_id=None):
    """每次 API 调用收尾的 message_delta（开了 --include-partial-messages 才有）：真实的 usage 只在这里"""
    return {
        "type": "stream_event",
        "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": usage},
        "parent_tool_use_id": parent_tool_use_id,
    }


def _three_assistant_events():
    """init + 第一次调用（text / tool_use 两个事件 + 收尾的 message_delta）+ 第二次调用（用量更大）

    模拟真实流的形状：assistant 事件自带的 usage 全是 0，真实用量在同一次调用收尾的 message_delta 里。
    """
    return [
        _init_event(),
        _assistant_event("msg_1", [{"type": "text", "text": "先看看文件"}], USAGE_ZERO),
        _assistant_event("msg_1", [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.txt"}}], USAGE_ZERO),
        _delta_event(USAGE_SMALL),
        _assistant_event("msg_2", [{"type": "text", "text": "看完了"}], USAGE_ZERO),
        _delta_event(USAGE_LARGE),
    ]


def _run_call_cli(monkeypatch, tmp_path, worker, events, *, exit_code=0, hang=0.0, **call_kwargs):
    """用真实的 SessionWorker._call_claude_cli 跑一次，把"claude 可执行文件"换成一个假脚本进程

    create_subprocess_exec 包一层：命令行最前面插上假脚本及其参数，bridge 拼的参数原样保留在后面。
    返回 (往外抛的错误文本或 None, 响应文本或 None, bridge 实际传给 claude 的 prompt 列表)。
    """
    script_path = tmp_path / "fake_claude.py"
    script_path.write_text(FAKE_CLAUDE_SCRIPT, encoding="utf-8")
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    real_spawn = asyncio.create_subprocess_exec
    spawned = []
    prompts = []

    async def fake_spawn(executable, *args, **kwargs):
        assert executable == sys.executable and "--output-format" in args and "stream-json" in args
        # 不开这个开关流里就没有 message_delta，用量取不到
        assert "--include-partial-messages" in args
        prompts.append(args[-1])  # bridge 把 prompt 放在最后一个位置参数
        process = await real_spawn(sys.executable, str(script_path), str(events_path), str(exit_code), str(hang), *args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    async def scenario():
        error = None
        response = None
        try:
            response = await worker._call_claude_cli(
                prompt=PROMPT,
                session_id=SESSION_ID,
                session_created=True,
                working_dir=str(tmp_path),
                username=USERNAME,
                user_id=USER_ID,
                is_dm=True,
                **call_kwargs,
            )
        except Exception as e:  # 测试要拿到 bridge 往外抛的错误文本
            error = str(e)
        # _call_claude_cli 没收掉的进程（比如被模拟成打断失败的）由测试自己收掉，不留到事件循环关闭之后
        for process in spawned:
            if process.returncode is None:
                process.kill()
            await process.wait()
        return error, response

    error, response = asyncio.run(scenario())
    return error, response, prompts


def test_call_cli_stores_last_usage_not_sum(tmp_path, queue, monkeypatch):
    """流里多次 API 调用：落库的是最后一次调用收尾 message_delta 的 input+cache_creation+cache_read

    不是累加，也不看 assistant 事件里全为 0 的 usage。
    """
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    error, response, prompts = _run_call_cli(
        monkeypatch, tmp_path, worker, _three_assistant_events(), session_key=session_key, message_id=message_id
    )

    assert error is None
    assert response == "先看看文件\n看完了"
    assert queue.sessions.get_context_usage(session_key) == 6000  # 不是 170（第一条）也不是 6340（累加）
    assert queue.get_message_context_usage(message_id) == 6000
    # 调用前库里没有用量：本轮不注入（本轮的用量要等这次调用结束才落库）
    assert prompts == [f"{USERNAME}（{USER_ID}）在 Discord 私聊中说：{PROMPT}"]


def test_call_cli_ignores_subagent_usage(tmp_path, queue, monkeypatch):
    """子代理（Agent 工具）的事件带 parent_tool_use_id，排在最后也不记：落库的是主线程最后一次调用的 usage"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    subagent_events = [
        {**_assistant_event("msg_sub_1", [{"type": "text", "text": "子代理在想"}], USAGE_ZERO),
         "parent_tool_use_id": "toolu_agent"},
        {**_assistant_event("msg_sub_2", [{"type": "tool_use", "id": "toolu_x", "name": "Read", "input": {}}], USAGE_ZERO),
         "parent_tool_use_id": "toolu_agent"},
        _delta_event(USAGE_LARGE, parent_tool_use_id="toolu_agent"),
    ]
    events = [
        _init_event(),
        _assistant_event("msg_1", [{"type": "text", "text": "派个子代理"}], USAGE_ZERO),
        {**_assistant_event("msg_1", [{"type": "tool_use", "id": "toolu_agent", "name": "Agent", "input": {}}], USAGE_ZERO),
         "parent_tool_use_id": None},  # 主线程事件的 parent_tool_use_id 为 null，照常处理
        _delta_event(USAGE_SMALL, parent_tool_use_id=None),
        *subagent_events,
    ]

    error, _, _ = _run_call_cli(
        monkeypatch, tmp_path, worker, events, session_key=session_key, message_id=message_id
    )

    assert error is None
    assert queue.sessions.get_context_usage(session_key) == 170  # 不是子代理的 6000
    assert queue.get_message_context_usage(message_id) == 170


def test_call_cli_ignores_unusable_usage(tmp_path, queue, monkeypatch):
    """message_delta 的 usage 没有输入类字段（算出来是 0）时不覆盖已有的有效值；其余 stream_event 一律忽略"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    events = [
        _init_event(),
        _assistant_event("msg_1", [{"type": "text", "text": "好"}], USAGE_LARGE),  # assistant 自带的 usage 不看
        _delta_event(USAGE_SMALL),
        {"type": "stream_event", "event": {"type": "message_start", "message": {"usage": USAGE_LARGE}},
         "parent_tool_use_id": None},
        _delta_event({"output_tokens": 500}),  # 只有输出：算 0，不覆盖
        {"type": "stream_event", "event": {"usage": USAGE_LARGE}},  # 没有 event.type
    ]

    error, _, _ = _run_call_cli(
        monkeypatch, tmp_path, worker, events, session_key=session_key, message_id=message_id
    )

    assert error is None
    assert queue.sessions.get_context_usage(session_key) == 170
    assert queue.get_message_context_usage(message_id) == 170


def test_call_cli_no_usage_when_stream_has_no_delta(tmp_path, queue, monkeypatch):
    """流里没有 message_delta（比如没开 --include-partial-messages）：不落库，也不报错"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    events = [_init_event(), _assistant_event("msg_1", [{"type": "text", "text": "好"}], USAGE_ZERO)]

    error, response, _ = _run_call_cli(
        monkeypatch, tmp_path, worker, events, session_key=session_key, message_id=message_id
    )

    assert error is None
    assert response == "好"
    assert queue.sessions.get_context_usage(session_key) is None
    assert queue.get_message_context_usage(message_id) is None


def test_call_cli_injects_context_line_when_enabled(tmp_path, queue, monkeypatch):
    """开关开 + 上一轮有用量：用量行拼在 prompt 最前面，空行隔开"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    queue.sessions.update_context_usage(session_key, 118729)
    worker = _make_worker(queue, tmp_path, context_prompt_enabled=True)

    _, _, prompts = _run_call_cli(
        monkeypatch, tmp_path, worker, _three_assistant_events(), session_key=session_key, message_id=message_id
    )

    assert len(prompts) == 1
    assert prompts[0].startswith("[上下文用量] 已用 12% / 1M Tokens\n\n")
    assert prompts[0].endswith(f"{USERNAME}（{USER_ID}）在 Discord 私聊中说：{PROMPT}")


def test_call_cli_no_injection_when_prompt_disabled(tmp_path, queue, monkeypatch):
    """context_prompt_enabled=False：库里有用量也不注入"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    queue.sessions.update_context_usage(session_key, 118729)
    worker = _make_worker(queue, tmp_path, context_prompt_enabled=False)

    _, _, prompts = _run_call_cli(
        monkeypatch, tmp_path, worker, _three_assistant_events(), session_key=session_key, message_id=message_id
    )

    assert prompts == [f"{USERNAME}（{USER_ID}）在 Discord 私聊中说：{PROMPT}"]


def test_call_cli_no_injection_for_system_tag(tmp_path, queue, monkeypatch):
    """系统注入消息：有用量也不注入，prompt 原样不动"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    queue.sessions.update_context_usage(session_key, 118729)
    worker = _make_worker(queue, tmp_path)

    _, _, prompts = _run_call_cli(
        monkeypatch, tmp_path, worker, _three_assistant_events(),
        session_key=session_key, message_id=message_id, message_tag=MessageTag.SYSTEM.value,
    )

    assert prompts == [PROMPT]


def test_call_cli_records_usage_when_claude_fails(tmp_path, queue, monkeypatch):
    """claude 返回错误码：异常照常往外抛，但已经发生的调用占用过的上下文照样落库"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    error, response, _ = _run_call_cli(
        monkeypatch, tmp_path, worker, _three_assistant_events(), exit_code=3,
        session_key=session_key, message_id=message_id,
    )

    assert response is None
    assert error == "经过 1 次调用尝试后仍然失败: Claude Code 返回错误码 3"
    assert queue.sessions.get_context_usage(session_key) == 6000
    assert queue.get_message_context_usage(message_id) == 6000


def test_call_cli_records_usage_when_aborted(tmp_path, queue, monkeypatch):
    """被打断：进程被杀、消息标记 aborted，但打断前已经收到的 usage 照样落库"""
    session_key = _make_session(queue, str(tmp_path))
    message_id = _add_message(queue)
    worker = _make_worker(queue, tmp_path)

    # 假脚本一次性输出 assistant 事件和收尾的 message_delta 后长眠；等序列落库（说明 usage 也已收到）后再"按下中止"
    real_is_aborting = queue.is_aborting

    def abort_once_streamed(message_id):
        if queue.get_message_sequences_stats(message_id)["total"] > 0:
            return True
        return real_is_aborting(message_id)

    monkeypatch.setattr(queue, "is_aborting", abort_once_streamed)
    events = [
        _init_event(),
        _assistant_event("msg_1", [{"type": "text", "text": "先看看文件"}], USAGE_ZERO),
        _delta_event(USAGE_SMALL),
    ]

    error, response, _ = _run_call_cli(
        monkeypatch, tmp_path, worker, events, hang=60, session_key=session_key, message_id=message_id
    )

    assert error is None
    assert response == "先看看文件"
    assert queue.is_message_aborted(message_id) is True
    assert queue.sessions.get_context_usage(session_key) == 170
    assert queue.get_message_context_usage(message_id) == 170


# ========== Discord 尾巴：_send_quota_notice 先上下文用量后智谱额度 ==========

class _FakeChannel:
    """记录 channel.send 内容的假 Discord 频道"""

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class _QueueStub:
    """只带尾巴发送用得到的方法的假消息队列"""

    def __init__(self, usage=None, aborted=False):
        self.usage = usage
        self.aborted = aborted

    def is_message_aborted(self, message_id):
        return self.aborted

    def get_message_context_usage(self, message_id):
        return self.usage


def _discord_sender(monkeypatch, *, usage=None, aborted=False, context_enabled=True, quota_enabled=True,
                    quota_entries=(QUOTA_ENTRY,)):
    """_send_quota_notice 的最小运行环境：假 config / 假队列，额度查询换成假函数（不发网络请求）

    返回 (sender, 额度接口调用记录, 模块日志记录器)
    """
    from bot.discord import discord_sequence_sender as module

    recorder = _Recorder()
    monkeypatch.setattr(module, "log", recorder)

    fetch_calls = []

    def fake_fetch():  # 真实的 fetch_quota_summary 是同步函数（生产代码经 asyncio.to_thread 调用）
        fetch_calls.append(1)
        return list(quota_entries) if quota_entries else None

    monkeypatch.setattr(module, "fetch_quota_summary", fake_fetch)

    sender = module.DiscordSequenceSenderMixin()
    sender.config = types.SimpleNamespace(
        context_monitor_enabled=context_enabled,
        quota_monitor_enabled=quota_enabled,
        context_window_tokens=DEFAULT_CONTEXT_WINDOW,
    )
    sender.message_queue = _QueueStub(usage=usage, aborted=aborted)
    return sender, fetch_calls, recorder


def test_discord_notice_sends_context_then_quota(monkeypatch):
    """两个开关都开：先发上下文用量（单条代码块），再发智谱额度"""
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == [f"```\n{CONTEXT_LINE_12}\n```", f"```\n{QUOTA_ENTRY}\n```"]
    assert fetch_calls == [1]


def test_discord_notice_context_only(monkeypatch):
    """只开上下文用量：不发智谱额度，也不去查额度接口"""
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729, quota_enabled=False)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == [f"```\n{CONTEXT_LINE_12}\n```"]
    assert fetch_calls == []


def test_discord_notice_quota_only(monkeypatch):
    """只开智谱额度：行为与改动前一致"""
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729, context_enabled=False)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == [f"```\n{QUOTA_ENTRY}\n```"]
    assert fetch_calls == [1]


def test_discord_notice_both_disabled(monkeypatch):
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729, context_enabled=False, quota_enabled=False)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == []
    assert fetch_calls == []


def test_discord_notice_without_usage_still_sends_quota(monkeypatch):
    """库里还没有这条消息的用量（或没传 message_id）：跳过上下文用量，智谱额度照发"""
    sender, _, _ = _discord_sender(monkeypatch, usage=None)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))
    asyncio.run(sender._send_quota_notice(channel))  # message_id=None

    assert channel.sent == [f"```\n{QUOTA_ENTRY}\n```", f"```\n{QUOTA_ENTRY}\n```"]


def test_discord_notice_skips_aborted_message(monkeypatch):
    """被打断的消息：两种提示都不发"""
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729, aborted=True)
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == []
    assert fetch_calls == []


def test_discord_notice_skips_without_channel(monkeypatch):
    """没有 channel 引用：什么都不发"""
    sender, fetch_calls, _ = _discord_sender(monkeypatch, usage=118729)

    asyncio.run(sender._send_quota_notice(None, message_id=1))

    assert fetch_calls == []


def test_discord_notice_never_raises(monkeypatch):
    """读用量出错：被外层 try/except 兜住，只打日志"""
    sender, _, recorder = _discord_sender(monkeypatch, usage=118729)

    def boom(message_id):
        raise RuntimeError("db boom")

    sender.message_queue.get_message_context_usage = boom
    channel = _FakeChannel()

    asyncio.run(sender._send_quota_notice(channel, message_id=1))

    assert channel.sent == []
    assert any("额度提示发送失败" in line and "db boom" in line for line in recorder.lines)


# ========== 微信尾巴：最后一条 text 的顺序是 正文 → 上下文用量 → 智谱额度 ==========

class _FakeWeixinClient:
    """记录 send_message 文本的假微信客户端"""

    def __init__(self):
        self.sent = []

    async def send_message(self, to_user_id=None, text=None, context_token=None):
        self.sent.append(text)


class _WeixinQueueStub:
    """只带 check_message_sequences 用得到的方法的假消息队列：一条消息、一条 text 序列（发完即完成）"""

    def __init__(self, usage=None):
        self.usage = usage
        self.sequences = [{"id": 7, "sequence_index": 0, "item_type": "text", "item_data": {"text": "这是正文"}}]
        self.messages = [{
            "id": 1, "discord_channel_id": 0, "discord_user_id": USER_ID, "is_dm": True,
            "channel_type": "weixin", "username": USERNAME, "context_token": "ctx-token",
        }]

    def get_messages_with_pending_sequences(self, channel_type, limit=1):
        return self.messages if self.sequences else []

    def get_pending_message_sequences(self, message_id, limit=1):
        return self.sequences

    def get_message_sequences_stats(self, message_id):
        return {"total": 1, "pending": 0, "sent": 1}

    def get_pending_tool_use_results(self):
        return []

    def is_ai_response_complete(self, message_id):
        return True

    def is_last_text_sequence(self, message_id, seq_id):
        return True

    def get_message_context_usage(self, message_id):
        return self.usage

    def incr_weixin_quota(self, username, token):
        return 1

    def mark_sequence_sent(self, seq_id):
        self.sequences = []

    def cleanup_message_sequences(self, message_id):
        pass

    def update_status(self, message_id, status, **kwargs):
        pass


def _weixin_sender(monkeypatch, *, usage=None, context_enabled=True, quota_enabled=True, quota_entries=(QUOTA_ENTRY,)):
    """check_message_sequences 的最小运行环境：假队列 / 假客户端，额度查询换成假函数"""
    from bot.weixin import weixin_sequence_sender as module

    monkeypatch.setattr(module, "log", _Recorder())

    def fake_fetch():  # 真实的 fetch_quota_summary 是同步函数（生产代码经 asyncio.to_thread 调用）
        return list(quota_entries) if quota_entries else None

    monkeypatch.setattr(module, "fetch_quota_summary", fake_fetch)

    sender = module.WeixinSequenceSenderMixin()
    sender.config = types.SimpleNamespace(
        quota_monitor_enabled=quota_enabled,
        context_monitor_enabled=context_enabled,
        context_window_tokens=DEFAULT_CONTEXT_WINDOW,
        quota_hint_enabled=False,  # 不拼"剩余可发送消息数量"，只看本功能的尾巴
        queue_send_interval=0,
    )
    sender.message_queue = _WeixinQueueStub(usage=usage)
    sender.running = True
    sender.accounts = [types.SimpleNamespace(wxid="wx-1", bot_id="bot-1")]
    sender.username_to_wxid = {USERNAME: "wx-1"}
    sender.clients = {"bot-1": _FakeWeixinClient()}
    sender.context_tokens = {USERNAME: "ctx-token"}
    sender.typing_tickets = {USERNAME: "typing-ticket"}
    sender.pending_messages = {}

    async def _no_op_typing(*args, **kwargs):
        return None

    sender._maintain_typing_indicator = _no_op_typing

    async def stop_typing(message_id):
        sender.running = False  # 完成流程走完后让循环退出

    sender.stop_typing_indicator = stop_typing
    return sender


@pytest.mark.parametrize(
    "usage, context_enabled, quota_enabled, expected",
    [
        (118729, True, True, f"这是正文\n\n`{CONTEXT_LINE_12}`\n\n`{QUOTA_ENTRY}`"),
        (118729, True, False, f"这是正文\n\n`{CONTEXT_LINE_12}`"),
        (118729, False, True, f"这是正文\n\n`{QUOTA_ENTRY}`"),
        (118729, False, False, "这是正文"),
        (None, True, False, "这是正文"),
    ],
)
def test_weixin_tail_order(monkeypatch, usage, context_enabled, quota_enabled, expected):
    """正文 → 上下文用量 → 智谱额度；开关关或没数据时对应的那段不拼"""
    sender = _weixin_sender(monkeypatch, usage=usage, context_enabled=context_enabled, quota_enabled=quota_enabled)

    asyncio.run(sender.check_message_sequences())

    assert sender.clients["bot-1"].sent == [expected]


# ========== /context 命令（Discord + 微信） ==========

class _FakeTree:
    """记录注册的斜杠命令：名字 → (description, 处理协程)"""

    def __init__(self):
        self.commands = {}

    def command(self, *, name, description):
        def decorator(func):
            self.commands[name] = (description, func)
            return func

        return decorator

    def context_menu(self, *, name):
        def decorator(func):
            return func

        return decorator


class _FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


def _make_interaction(channel_id):
    """频道（非私聊）里的交互：频道对象只需要有 id"""
    return types.SimpleNamespace(
        channel=types.SimpleNamespace(id=channel_id),
        user=types.SimpleNamespace(id=USER_ID),
        response=_FakeResponse(),
    )


def _discord_commands(queue, tmp_path):
    """跑一遍真实的 add_commands，拿到注册好的 /context 处理协程"""
    from bot.discord.discord_commands import DiscordCommandsMixin

    harness = DiscordCommandsMixin()
    harness.tree = _FakeTree()
    harness.config = types.SimpleNamespace(
        working_directory=str(tmp_path), allowed_dm_users=[], context_window_tokens=DEFAULT_CONTEXT_WINDOW,
    )
    harness.message_queue = queue
    asyncio.run(harness.add_commands())
    return harness


def test_discord_context_command_with_data(tmp_path, queue):
    harness = _discord_commands(queue, tmp_path)
    queue.get_or_create_session(str(tmp_path), channel_id=555, user_id=None, is_dm=False)
    queue.sessions.update_context_usage("channel_555", 118729)
    description, handler = harness.tree.commands["context"]
    interaction = _make_interaction(channel_id=555)

    assert description == "查询当前会话的上下文用量"
    asyncio.run(handler(interaction))

    ((_, kwargs),) = interaction.response.sent
    embed = kwargs["embed"]
    assert embed.title == "🧠 上下文用量"
    assert embed.color == discord.Color.green()
    assert embed.description == "`▓░░░░░░░░░` 上下文 已用 12% / 1M Tokens"


def test_discord_context_command_without_data(tmp_path, queue):
    harness = _discord_commands(queue, tmp_path)
    _, handler = harness.tree.commands["context"]
    interaction = _make_interaction(channel_id=556)

    asyncio.run(handler(interaction))

    ((_, kwargs),) = interaction.response.sent
    assert kwargs["embed"].title == "🧠 上下文用量"
    assert kwargs["embed"].description == "暂无数据：当前会话还没有产生过回复"


def _weixin_commands(queue, tmp_path):
    """微信命令 Mixin 的最小运行环境：_send_direct_message 换成记录器"""
    from bot.weixin.weixin_commands import WeixinCommandsMixin

    harness = WeixinCommandsMixin()
    harness.config = types.SimpleNamespace(working_directory=str(tmp_path), context_window_tokens=DEFAULT_CONTEXT_WINDOW)
    harness.message_queue = queue
    harness.username_to_userid = {USERNAME: USER_ID}
    harness.clients = {"bot-1": object()}
    sent = []

    async def fake_send(to_user_id, account_bot_id, text):
        sent.append(text)

    harness._send_direct_message = fake_send
    return harness, sent


def test_weixin_context_command_with_data(tmp_path, queue):
    harness, sent = _weixin_commands(queue, tmp_path)
    queue.get_or_create_session(str(tmp_path), channel_id=None, user_id=USER_ID, is_dm=True)
    queue.sessions.update_context_usage(f"dm_{USER_ID}", 118729)

    asyncio.run(harness._cmd_context(USERNAME, "bot-1"))

    assert sent == ["**🧠 上下文用量**\n\n`▓░░░░░░░░░` 上下文 已用 12% / 1M Tokens"]


def test_weixin_context_command_without_data(tmp_path, queue):
    harness, sent = _weixin_commands(queue, tmp_path)

    asyncio.run(harness._cmd_context(USERNAME, "bot-1"))

    assert sent == ["**🧠 上下文用量**\n\n暂无数据：当前会话还没有产生过回复"]


def test_weixin_context_command_unknown_user(tmp_path, queue):
    harness, sent = _weixin_commands(queue, tmp_path)

    asyncio.run(harness._cmd_context("陌生人", "bot-1"))

    assert sent == ["**⚠️ 未找到用户配置**\n\n用户 `陌生人` 的配置不存在"]


def test_weixin_context_dispatch_and_help(tmp_path, queue):
    """/context 能分发到 _cmd_context；帮助文本里 /context 排在 /quota 后面"""
    harness, sent = _weixin_commands(queue, tmp_path)

    asyncio.run(harness._handle_command(USERNAME, "/context", "bot-1"))
    assert sent == ["**🧠 上下文用量**\n\n暂无数据：当前会话还没有产生过回复"]

    asyncio.run(harness._handle_command(USERNAME, "/noop", "bot-1"))
    assert "`/quota` - 查询剩余额度\n\n`/context` - 查询上下文用量\n\n`/queuemode`" in sent[-1]
