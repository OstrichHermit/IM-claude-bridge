"""
孤儿进程回收测试（shared/process_utils.py + 三处接入点）

真杀只针对本测试自己起的、命令行带唯一 marker 的 dummy 进程；对真实进程只做只读的 dry-run。
不读写真实的 shared/messages.db，也不往线上日志文件里写（SessionWorker 的日志一律换成 Recorder）。
"""
import asyncio
import ctypes
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
import types
from pathlib import Path

import psutil
import pytest

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from bridge import session_worker
from shared import process_utils
from shared.process_utils import (
    CLAUDE_PROCESS_NAME,
    find_orphan_processes,
    get_ancestor_pids,
    get_descendants,
    kill_process_tree,
    kill_process_tree_detail,
    list_processes,
    reclaim_orphan_processes,
)

IS_WIN = sys.platform == "win32"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
needs_windows = pytest.mark.skipif(not IS_WIN, reason="只在 Windows 上验证杀进程树")

# Windows 的 pid 永远是 4 的倍数：伪造的 pid 取 4k+1/4k+2，保证系统里绝不存在同号进程
FAKE_PARENT, FAKE_CHILD, FAKE_EXTRA = 999201, 999202, 999203
SESSION_ID = "11111111-2222-3333-4444-555555555555"

_MARKERS = set()  # 本模块用过的所有 marker，收尾时兜底清理


# ========== 工具 ==========

class _Recorder:
    """记录日志行的假 logger"""

    def __init__(self):
        self.lines = []

    def log(self, message):
        self.lines.append(message)


def _marker(prefix):
    value = f"{prefix}_{secrets.token_hex(6)}"
    _MARKERS.add(value)
    return value


def _spawn(*args, pipe=False):
    """起一个 dummy python 进程"""
    return subprocess.Popen(
        [sys.executable, *map(str, args)],
        stdout=subprocess.PIPE if pipe else None,
        text=pipe,
        creationflags=CREATE_NO_WINDOW,
    )


def _marked_pids():
    """命令行里带本模块 marker 的进程 → {marker: [pid, ...]}"""
    found = {}
    for proc in list_processes():
        if not (proc["name"] or "").lower().startswith("python"):
            continue
        process_utils.fill_process_detail(proc, cmdline=True)
        for marker in _MARKERS:
            if marker in proc["cmdline"]:
                found.setdefault(marker, []).append(proc["pid"])
    return found


def _wait_until(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return predicate()


def _fake(pid, ppid, create_time, name=CLAUDE_PROCESS_NAME, cmdline=None):
    """伪造的进程 dict（不碰真实进程）"""
    if cmdline is None:
        cmdline = f"claude.exe -p --verbose --output-format stream-json -r {SESSION_ID} 请帮我看看这个文件"
    return {"pid": pid, "ppid": ppid, "name": name, "create_time": create_time, "cmdline": cmdline}


@pytest.fixture(scope="session")
def dummy_dir(tmp_path_factory):
    """dummy 脚本：leaf、launcher、parent，祖先链用的 chain_launcher / chain_x / chain_killer，模拟启动超时的 silent"""
    directory = tmp_path_factory.mktemp("orphan_dummies")
    scripts = {
        # argv: marker depth；depth>0 时再拉一个 depth-1 的 leaf，然后长眠
        "leaf.py": (
            "import subprocess, sys, time\n"
            "marker, depth = sys.argv[1], int(sys.argv[2])\n"
            "if depth > 0:\n"
            "    subprocess.Popen([sys.executable, __file__, marker, str(depth - 1)], creationflags=0x08000000)\n"
            "time.sleep(600)\n"
        ),
        # argv: leaf marker depth；拉起 leaf 后打印它的 pid 并立刻退出（短命的父进程）
        "launcher.py": (
            "import subprocess, sys\n"
            "p = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]], creationflags=0x08000000)\n"
            "print(p.pid, flush=True)\n"
        ),
        # argv: leaf marker；拉起一个带 marker 的孙进程，打印它的 pid，然后长眠
        "parent.py": (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], '0'], creationflags=0x08000000)\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(600)\n"
        ),
        # argv: leaf marker；拉起一个带 marker 的子进程后静默长眠（stdout 一个字都不写 → claude 迟迟不发 init）
        "silent.py": (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], '0'], creationflags=0x08000000)\n"
            "time.sleep(600)\n"
        ),
        # argv: 脚本及其参数；拉起后立刻退出（让它成为"父进程已死"的孤儿，和线上重启后的形态一致）
        "chain_launcher.py": (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, *sys.argv[1:]], creationflags=0x08000000)\n"
        ),
        # argv: marker leaf killer ready result root；X：拉起 D（sleeper）和 K（杀手，把自己的 pid 告诉它）后长眠
        "chain_x.py": (
            "import os, subprocess, sys, time\n"
            "marker, leaf, killer, ready, result, root = sys.argv[1:7]\n"
            "d = subprocess.Popen([sys.executable, leaf, marker, '0'], creationflags=0x08000000)\n"
            "with open(ready + '.tmp', 'w') as fh:\n"
            "    fh.write(f'{os.getpid()} {d.pid}')\n"
            "os.replace(ready + '.tmp', ready)\n"
            "subprocess.Popen([sys.executable, killer, result, root, marker, str(os.getpid())],\n"
            "                 creationflags=0x08000000)\n"
            "time.sleep(600)\n"
        ),
        # argv: result root marker target_pid；K：对自己的父进程 X 调用杀树（X 是 K 的祖先），结果直接写进 result
        "chain_killer.py": (
            "import json, os, sys, time\n"
            "result, root, marker, target = sys.argv[1:5]\n"
            "sys.path.insert(0, root)\n"
            "from shared import process_utils\n"
            "pruned = [os.getpid()] + [p['pid'] for p in process_utils.get_descendants(os.getpid())]\n"
            "expected = [p['pid'] for p in process_utils.get_descendants(int(target)) if p['pid'] not in pruned]\n"
            "taskkilled = []\n"
            "real_taskkill = process_utils._taskkill_tree\n"
            "process_utils._taskkill_tree = lambda pid: taskkilled.append(pid) or real_taskkill(pid)\n"
            "status, count = process_utils.kill_process_tree_detail(\n"
            "    int(target), verify={'name': 'python.exe', 'cmdline_contains': [marker]})\n"
            "with open(result + '.tmp', 'w') as fh:\n"
            "    json.dump({'status': status, 'count': count, 'expected': expected,\n"
            "               'taskkill': taskkilled, 'killer_pid': os.getpid()}, fh)\n"
            "os.replace(result + '.tmp', result)\n"
            "time.sleep(1.0)   # 留出时间：确认没有迟到的补杀落到自己（祖先）头上\n"
            "with open(result + '.done', 'w') as fh:\n"
            "    fh.write('ok')\n"
        ),
    }
    for filename, source in scripts.items():
        (directory / filename).write_text(source, encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def _no_dummy_left():
    """每个测试结束后兜底：带 marker 的 dummy 进程一个都不许留"""
    yield
    for pids in _marked_pids().values():
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
    time.sleep(0.3)
    assert not _marked_pids(), f"测试结束后仍有带 marker 的 dummy 进程残留: {_marked_pids()}"


def _launch_orphan_tree(dummy_dir, marker):
    """短命的启动器 A 拉起 B（depth=1，B 再拉起 C）后立刻退出 → B 的父进程已死、C 的父进程 B 还活着

    返回 (A 的 pid, B 的 pid, C 的 pid)
    """
    a = _spawn(dummy_dir / "launcher.py", dummy_dir / "leaf.py", marker, 1, pipe=True)
    b_pid = int(a.communicate(timeout=60)[0].strip())
    assert a.wait(timeout=60) == 0
    c_pids = _wait_until(lambda: [pid for pid in _marked_pids().get(marker, []) if pid != b_pid])
    assert c_pids, "孙进程 C 没起来"
    return a.pid, b_pid, c_pids[0]


async def _wait_async(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    return predicate()


def _claude_with_alive_parent():
    """独立于被测代码、直接用 psutil 算：当前活着且父进程也活着（并比它先创建）的 claude.exe → {pid: create_time}"""
    result = {}
    for proc in psutil.process_iter(["pid", "name"]):
        if (proc.info["name"] or "").lower() != "claude.exe":
            continue
        try:
            parent = proc.parent()
            if parent is not None and parent.is_running() and parent.create_time() <= proc.create_time():
                result[proc.pid] = proc.create_time()
        except psutil.Error:
            continue
    return result


# ========== 1. 假孤儿树：查找 / dry-run / 真杀 ==========

@needs_windows
def test_orphan_tree_find_dryrun_then_kill(dummy_dir):
    marker = _marker("ORPHAN_TEST")
    a_pid, b_pid, c_pid = _launch_orphan_tree(dummy_dir, marker)

    # 恰好只返回 B：不含 C（父进程还活着），也不含测试进程自己
    orphans = find_orphan_processes("python.exe", (marker,))
    assert [p["pid"] for p in orphans] == [b_pid]
    assert orphans[0]["ppid"] == a_pid and os.getpid() not in [p["pid"] for p in orphans]
    descendants = [p["pid"] for p in get_descendants(b_pid)]
    assert descendants and c_pid in descendants, "C 应该是 B 的后代"

    # dry_run=True：只列不杀，B、C 都还活着
    records = reclaim_orphan_processes(
        log=_Recorder(), process_name="python.exe", signature_patterns=(marker,), dry_run=True)
    assert [(r["pid"], r["killed"]) for r in records] == [(b_pid, False)]
    assert psutil.pid_exists(b_pid) and psutil.pid_exists(c_pid)

    # 身份校验通过 → 连 C 一起杀掉
    assert kill_process_tree(b_pid, verify={"name": "python.exe", "cmdline_contains": [marker]}) is True
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B 或 C 没被杀干净"


@needs_windows
def test_reclaim_orphans_kills_dummy_tree_and_logs_no_cmdline(dummy_dir):
    marker = _marker("RECLAIM_TEST")
    _, b_pid, _ = _launch_orphan_tree(dummy_dir, marker)
    recorder = _Recorder()

    records = reclaim_orphan_processes(
        log=recorder, process_name="python.exe", signature_patterns=(marker,))

    assert [(r["pid"], r["killed"]) for r in records] == [(b_pid, True)]
    record = records[0]
    assert set(record) == {"pid", "ppid", "create_time", "session_id", "descendants", "killed"}
    assert record["descendants"] >= 1 and record["session_id"] == "" and record["create_time"]
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B/C 没被杀干净"

    assert "♻️ 发现 1 个上次运行遗留的孤儿 python 进程，正在回收..." in recorder.lines
    killed_line = next(line for line in recorder.lines
                       if line.startswith(f"🗑️ 已回收孤儿 python 进程 PID={b_pid}"))
    assert "启动于 " in killed_line and "连带 " in killed_line
    # 日志里不能出现命令行/prompt 原文（marker 只存在于 dummy 的命令行里）
    assert all(marker not in line and str(dummy_dir) not in line for line in recorder.lines)


def test_reclaim_orphans_without_orphans_logs_and_returns_empty(capsys):
    recorder = _Recorder()
    assert reclaim_orphan_processes(log=recorder, process_name="nonexistent_xyz.exe", all_procs=[]) == []
    assert recorder.lines == ["✓ 没有遗留的孤儿 nonexistent_xyz 进程"]

    # log=None 时用 print
    assert reclaim_orphan_processes(process_name="nonexistent_xyz.exe", all_procs=[]) == []
    assert "✓ 没有遗留的孤儿 nonexistent_xyz 进程" in capsys.readouterr().out


def test_reclaim_orphans_never_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(process_utils, "find_orphan_processes", boom)
    recorder = _Recorder()
    assert reclaim_orphan_processes(log=recorder, all_procs=[_fake(FAKE_CHILD, FAKE_PARENT, 2.0)]) == []
    assert any(line.startswith("⚠️ 回收孤儿进程时出错") for line in recorder.lines)


def test_reclaim_orphans_records_ancestor_partial_kill_as_success(monkeypatch):
    """孤儿是当前进程的祖先（restart.bat 是从这个会话里拉起来的）：不再跳过，剪枝回收后根进程杀掉了就算回收成功"""
    ancestor = _fake(FAKE_PARENT, 1, 100.0)
    me = {"pid": os.getpid(), "ppid": FAKE_PARENT, "name": "python.exe", "create_time": 200.0, "cmdline": "pytest"}
    stubborn = _fake(FAKE_EXTRA, 1, 110.0)   # 另一个孤儿：回收之后根进程还活着
    outcomes = {FAKE_PARENT: ("killed_partial", 2), FAKE_EXTRA: ("failed", 0)}
    calls = []

    def fake_kill(pid, verify=None, all_procs=None):
        calls.append((pid, verify, all_procs))
        return outcomes[pid]

    monkeypatch.setattr(process_utils, "kill_process_tree_detail", fake_kill)
    recorder = _Recorder()

    records = reclaim_orphan_processes(log=recorder, all_procs=[ancestor, me, stubborn])

    assert [(r["pid"], r["killed"], r["descendants"]) for r in records] == [
        (FAKE_PARENT, True, 2), (FAKE_EXTRA, False, 0)]
    assert [pid for pid, _, _ in calls] == [FAKE_PARENT, FAKE_EXTRA], "是祖先也不该跳过"
    assert all(verify == {"name": CLAUDE_PROCESS_NAME, "cmdline_contains": ["--output-format stream-json"]}
               for _, verify, _ in calls)
    assert any(line.startswith(f"🗑️ 已回收孤儿 claude 进程 PID={FAKE_PARENT}（启动于 ")
               and line.endswith("；它是当前进程的祖先，只终止主进程及其余 2 个后代，保留通往当前进程的链路）")
               for line in recorder.lines), recorder.lines
    assert f"⚠️ 回收孤儿进程失败 PID={FAKE_EXTRA}" in recorder.lines
    assert not any("跳过" in line for line in recorder.lines)


# ========== 2. PID 复用判定（伪造进程列表，不碰真实进程） ==========

def test_find_orphans_parent_pid_reuse_rules():
    child = _fake(FAKE_CHILD, FAKE_PARENT, 200.0)
    signature = ("--output-format stream-json",)

    def parent(create_time):
        # 伪造的父进程名字故意不匹配过滤条件，它只用来考验"父进程是否存活"的判定
        return _fake(FAKE_PARENT, 1, create_time, name="pythonw.exe")

    # 父进程不在列表 → 孤儿
    assert find_orphan_processes("claude.exe", signature, all_procs=[child]) == [child]
    # 父进程比子进程晚创建 → PID 被复用，真正的父进程已死 → 孤儿
    assert find_orphan_processes("claude.exe", signature, all_procs=[child, parent(300.0)]) == [child]
    # 父进程比子进程先创建（同一时刻也算）→ 不是孤儿
    assert find_orphan_processes("claude.exe", signature, all_procs=[child, parent(100.0)]) == []
    assert find_orphan_processes("claude.exe", signature, all_procs=[child, parent(200.0)]) == []
    # 任一方 create_time 取不到 → 按父进程存活处理（宁可不杀）
    assert find_orphan_processes("claude.exe", signature, all_procs=[child, parent(None)]) == []
    assert find_orphan_processes(
        "claude.exe", signature,
        all_procs=[dict(child, create_time=None), parent(100.0)]) == []


def test_find_orphans_filters():
    upper = _fake(FAKE_CHILD, 1, 200.0, name="CLAUDE.EXE")                      # 名字不区分大小写
    partial = _fake(FAKE_EXTRA, 1, 200.0, cmdline="claude.exe -p --output-format json")   # 特征不全
    signature = ("--output-format stream-json",)
    assert find_orphan_processes("claude.exe", signature, all_procs=[upper, partial]) == [upper]
    assert find_orphan_processes(
        "claude.exe", ("--output-format stream-json", "--verbose"), all_procs=[upper, partial]) == [upper]
    assert find_orphan_processes("claude.exe", (), all_procs=[upper, partial]) == [upper, partial]
    assert find_orphan_processes(
        "claude.exe", (), all_procs=[upper, partial], exclude_pids=[FAKE_CHILD]) == [partial]

    # 当前进程即使父进程"不在列表"里也被排除
    me = {"pid": os.getpid(), "ppid": FAKE_PARENT, "name": "python.exe", "create_time": 1.0, "cmdline": "x"}
    assert find_orphan_processes("python.exe", (), all_procs=[me]) == []


def test_get_descendants_bfs_and_stale_link():
    #        1
    #      /   \
    #     2     3
    #     |    / \
    #     4   5   6      7 声称 1 是它爹却比 1 还早创建（PID 复用留下的假链接）
    procs = [
        _fake(1, 0, 1.0, name="a"), _fake(2, 1, 2.0, name="b"), _fake(3, 1, 2.5, name="c"),
        _fake(4, 2, 3.0, name="d"), _fake(5, 3, 3.5, name="e"), _fake(6, 3, 4.0, name="f"),
        _fake(7, 1, 0.5, name="old"), _fake(8, 0, 9.0, name="other"),
    ]
    assert [p["pid"] for p in get_descendants(1, all_procs=procs)] == [2, 3, 4, 5, 6]
    assert [p["pid"] for p in get_descendants(3, all_procs=procs)] == [5, 6]
    assert get_descendants(4, all_procs=procs) == []
    assert get_descendants(999999, all_procs=procs) == []


def test_descendants_prunes_ancestor_subtree():
    """剪枝回收的遍历规则：当前进程的祖先一出现就整枝剪掉，它名下的子树（哪怕不是祖先）都不进结果；其余后代照常"""
    #            1 = X（目标）
    #      /     |      \
    #     2      3       4 = 当前进程的祖先（通往当前进程的那条链）
    #     |     / \     /  \
    #     5    6   7   8    9   （8 = 当前进程；9 = 祖先名下的另一支，同样不碰）
    procs = [_fake(pid, ppid, float(pid), name=f"p{pid}") for pid, ppid in
             [(1, 0), (2, 1), (3, 1), (4, 1), (5, 2), (6, 3), (7, 3), (8, 4), (9, 4)]]

    kept, stale = process_utils._descendants(1, procs, 1.0, prune={4})
    assert [p["pid"] for p in kept] == [2, 3, 5, 6, 7] and stale is False
    # 祖先在更深处才出现：只剪它那一枝
    kept, _ = process_utils._descendants(1, procs, 1.0, prune={7})
    assert [p["pid"] for p in kept] == [2, 3, 4, 5, 6, 8, 9]
    # 不给 prune 就是原来的全量后代
    assert [p["pid"] for p in process_utils._descendants(1, procs, 1.0)[0]] == [2, 3, 4, 5, 6, 7, 8, 9]


def test_get_ancestor_pids_walks_ppid_chain():
    me = {"pid": os.getpid(), "ppid": FAKE_CHILD, "name": "me", "create_time": 500.0, "cmdline": ""}
    chain = [_fake(FAKE_CHILD, FAKE_EXTRA, 400.0, name="p1"),
             _fake(FAKE_EXTRA, FAKE_PARENT, 300.0, name="p2"),
             _fake(FAKE_PARENT, 1, 200.0, name="p3")]
    assert get_ancestor_pids(chain + [me]) == {FAKE_CHILD, FAKE_EXTRA, FAKE_PARENT}

    # 中间一环的"父"比子还晚创建（PID 复用）→ 链在这里断
    reused = [_fake(FAKE_CHILD, FAKE_EXTRA, 400.0, name="p1"),
              _fake(FAKE_EXTRA, FAKE_PARENT, 600.0, name="p2-reused")]
    assert get_ancestor_pids(reused + [me]) == {FAKE_CHILD}
    # 当前进程不在列表 → 空
    assert get_ancestor_pids(chain) == set()


def test_extract_session_id():
    other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert process_utils.extract_session_id(f"claude.exe -p -r {SESSION_ID} 请帮我做点事 --session-id {other}") == SESSION_ID
    assert process_utils.extract_session_id(f"claude.exe --session-id {other} -p") == other
    assert process_utils.extract_session_id("claude.exe -p -r nope") == ""
    assert process_utils.extract_session_id("") == "" and process_utils.extract_session_id(None) == ""


# ========== 3. 身份校验防误杀 ==========

@needs_windows
def test_kill_tree_refuses_mismatched_verify(dummy_dir):
    marker = _marker("OTHER_TEST")
    d = _spawn(dummy_dir / "leaf.py", marker, 0)
    assert _wait_until(lambda: marker in _marked_pids()), "dummy 没起来"

    # 命令行特征对不上 → 不杀
    assert kill_process_tree(d.pid, verify={"name": "python.exe", "cmdline_contains": [marker + "_nope"]}) is False
    # 进程名对不上 → 不杀
    assert kill_process_tree(d.pid, verify={"name": CLAUDE_PROCESS_NAME, "cmdline_contains": [marker]}) is False
    assert d.poll() is None, "校验不通过却被杀了"

    # 对得上才杀（配置里写不带 .exe 的名字也认）
    assert kill_process_tree(d.pid, verify={"name": "python", "cmdline_contains": [marker]}) is True
    assert d.wait(timeout=60) is not None


@needs_windows
def test_kill_tree_on_missing_pid():
    assert kill_process_tree(FAKE_PARENT) is False
    assert kill_process_tree_detail(FAKE_PARENT) == ("gone", 0)
    assert kill_process_tree_detail(FAKE_PARENT, verify={"name": "python.exe"}) == ("gone", 0)


@needs_windows
def test_kill_tree_uses_taskkill_t_when_tree_is_clean(dummy_dir, monkeypatch):
    marker = _marker("TASKKILL_TEST")
    _, b_pid, c_pid = _launch_orphan_tree(dummy_dir, marker)
    # 只喂 B 的真实子树，确保没有陈旧链接干扰
    snapshot = list_processes()
    root = next(p for p in snapshot if p["pid"] == b_pid)
    tree = [root] + get_descendants(b_pid, all_procs=snapshot)
    assert c_pid in [p["pid"] for p in tree[1:]]

    calls = []
    real_taskkill = process_utils._taskkill_tree
    monkeypatch.setattr(process_utils, "_taskkill_tree", lambda pid: calls.append(pid) or real_taskkill(pid))

    status, count = kill_process_tree_detail(
        b_pid, verify={"name": "python.exe", "cmdline_contains": [marker]}, all_procs=tree)

    assert (status, count) == ("killed", len(tree) - 1)
    assert calls == [b_pid]
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B/C 没被杀干净"


@needs_windows
def test_kill_tree_avoids_taskkill_t_on_stale_ppid_link(dummy_dir, monkeypatch):
    """有进程声称 B 是它爹、却比 B 还早创建 → taskkill /T 会顺着误杀它，必须改走核实后的 psutil 杀"""
    marker = _marker("STALE_TEST")
    _, b_pid, c_pid = _launch_orphan_tree(dummy_dir, marker)
    snapshot = list_processes()
    root = next(p for p in snapshot if p["pid"] == b_pid)
    tree = [root] + get_descendants(b_pid, all_procs=snapshot)
    b_create_time = process_utils.fill_process_detail(root)["create_time"]
    unrelated = {"pid": 999205, "ppid": b_pid, "name": "someapp.exe",
                 "create_time": b_create_time - 100, "cmdline": ""}

    def forbidden(pid):
        raise AssertionError("有陈旧链接时不该用 taskkill /T")

    monkeypatch.setattr(process_utils, "_taskkill_tree", forbidden)

    status, count = kill_process_tree_detail(
        b_pid, verify={"name": "python.exe", "cmdline_contains": [marker]}, all_procs=tree + [unrelated])

    assert status == "killed" and count == len(tree) - 1, "无关进程不该被算进后代"
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B/C 没被杀干净"


@needs_windows
def test_kill_tree_falls_back_to_psutil_when_taskkill_unavailable(dummy_dir, monkeypatch):
    """taskkill 启动失败/超时也不能漏：改用 psutil 把核实过的后代逐个杀掉"""
    marker = _marker("TASKKILL_FAIL_TEST")
    _, b_pid, _ = _launch_orphan_tree(dummy_dir, marker)

    def no_taskkill(*args, **kwargs):
        raise OSError("taskkill 不可用")

    monkeypatch.setattr(process_utils.subprocess, "run", no_taskkill)

    assert kill_process_tree(b_pid, verify={"name": "python.exe", "cmdline_contains": [marker]}) is True
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B/C 没被杀干净"


@needs_windows
def test_kill_tree_cleans_up_descendants_missed_by_taskkill(dummy_dir, monkeypatch):
    """taskkill 漏杀了后代（只杀掉根进程）→ 从最深一层往上用 psutil 补杀"""
    marker = _marker("LEAK_TEST")
    _, b_pid, c_pid = _launch_orphan_tree(dummy_dir, marker)
    monkeypatch.setattr(process_utils, "_taskkill_tree", lambda pid: psutil.Process(pid).kill())  # 模拟：只杀掉根

    patched = []
    real_psutil_kill = process_utils._psutil_kill

    def spy_psutil_kill(pid, create_time=None):
        patched.append(pid)
        return real_psutil_kill(pid, create_time)

    monkeypatch.setattr(process_utils, "_psutil_kill", spy_psutil_kill)

    assert kill_process_tree(b_pid, verify={"name": "python.exe", "cmdline_contains": [marker]}) is True
    assert c_pid in patched, "漏掉的 C 没有被补杀"
    assert _wait_until(lambda: not _marked_pids().get(marker)), "B/C 没被杀干净"


@needs_windows
def test_kill_tree_prunes_own_ancestor_chain(dummy_dir):
    """真实祖先链：L 拉起 X 后退出；X 拉起 D（sleeper）和 K（杀手）后长眠；K 对自己的父进程 X 调用杀树。

    X 是 K 的祖先：不能 taskkill /T（会把 K 连同整条链路一起杀掉），要剪枝回收——X、D 都死，K 安然跑完。
    """
    marker = _marker("ANCESTOR_TEST")
    ready = dummy_dir / f"chain_ready_{marker}.txt"
    result = dummy_dir / f"chain_result_{marker}.json"
    launcher = _spawn(dummy_dir / "chain_launcher.py", dummy_dir / "chain_x.py", marker,
                      dummy_dir / "leaf.py", dummy_dir / "chain_killer.py", ready, result, PROJECT_ROOT)
    assert launcher.wait(timeout=60) == 0, "启动器 L 没有正常退出"
    assert _wait_until(lambda: Path(f"{result}.done").exists()), \
        "杀手 K 没有跑完：要么 X 没被杀掉，要么 K 自己被连坐杀了"

    x_pid, d_pid = (int(v) for v in ready.read_text().split())
    out = json.loads(result.read_text())
    k_pid = out["killer_pid"]

    assert out["status"] == "killed_partial", out
    assert d_pid in out["expected"] and k_pid not in out["expected"]
    assert out["count"] == len(out["expected"]) >= 1, "数量应是实际杀掉的后代数（被剪掉的那条链路不算）"
    assert out["taskkill"] == [], "目标是当前进程的祖先时不许用 taskkill"
    assert not psutil.pid_exists(x_pid), "X 没被杀掉"
    assert not psutil.pid_exists(d_pid), "D 没被杀掉"
    assert all(not psutil.pid_exists(pid) for pid in out["expected"]), "还有没杀干净的后代"
    # K 写完结果 1 秒后才写 .done：它作为祖先链上的一环全程没被杀，最后是自己正常退出的
    assert _wait_until(lambda: not psutil.pid_exists(k_pid)), "K 没有自行退出"
    assert not _marked_pids().get(marker), "还有带 marker 的 dummy 进程残留"


@needs_windows
def test_kill_tree_pruned_reports_failed_when_root_survives(dummy_dir, monkeypatch):
    """剪枝回收之后根进程还活着（比如最后核对身份没通过）→ 如实报 failed，不谎报成功"""
    marker = _marker("PRUNE_FAIL_TEST")
    root = _spawn(dummy_dir / "parent.py", dummy_dir / "leaf.py", marker)
    child_pids = _wait_until(lambda: [pid for pid in _marked_pids().get(marker, []) if pid != root.pid])
    assert child_pids, "子进程没起来"

    def must_not_taskkill(pid):
        raise AssertionError("目标是当前进程的祖先时不该用 taskkill /T")

    monkeypatch.setattr(process_utils, "_taskkill_tree", must_not_taskkill)
    monkeypatch.setattr(process_utils, "_kill_root_verified", lambda pid, root_ct, verify: None)   # 模拟：根进程杀不掉
    # 伪造一条"当前进程 ← 假的中间环 ← 根进程"的祖先链（中间环的 pid 系统里不存在）
    link = {"pid": FAKE_EXTRA, "ppid": root.pid, "name": "python.exe", "create_time": None, "cmdline": ""}
    me = {"pid": os.getpid(), "ppid": FAKE_EXTRA, "name": "python.exe", "create_time": None, "cmdline": "pytest"}
    procs = [p for p in list_processes() if p["pid"] != os.getpid()] + [me, link]

    status, count = kill_process_tree_detail(
        root.pid, verify={"name": "python.exe", "cmdline_contains": [marker]}, all_procs=procs)

    assert status == "failed" and count >= 1, (status, count)
    assert all(not psutil.pid_exists(pid) for pid in child_pids), "其余后代还是照常要杀掉"
    assert root.poll() is None

    monkeypatch.undo()   # 撤掉上面"根进程杀不掉"的模拟：它不是谁的祖先，普通杀树照常能收掉
    assert kill_process_tree(root.pid, verify={"name": "python.exe", "cmdline_contains": [marker]}) is True


# ========== 4. 真实系统上的只读检查 ==========

def test_list_processes_real_snapshot_contains_self():
    procs = list_processes()
    assert len(procs) > 5
    me = next(p for p in procs if p["pid"] == os.getpid())
    assert set(me) == {"pid", "ppid", "name", "create_time", "cmdline"}
    assert me["name"].lower().startswith("python")

    process_utils.fill_process_detail(me, cmdline=True)
    reference = psutil.Process(os.getpid())
    assert me["ppid"] == reference.ppid()
    assert me["create_time"] == pytest.approx(reference.create_time())
    assert "pytest" in me["cmdline"]

    # 快照里的 pid/ppid 要和 psutil 对得上（抽 python 进程逐个核对）
    checked = 0
    for proc in procs:
        if not (proc["name"] or "").lower().startswith("python"):
            continue
        try:
            assert proc["ppid"] == psutil.Process(proc["pid"]).ppid(), proc
            checked += 1
        except psutil.NoSuchProcess:
            continue  # 两次查询之间退出了
    assert checked >= 1


@needs_windows
def test_snapshot_toolhelp_reads_last_error_before_close_handle(monkeypatch):
    """快照是否完整的判断（ERROR_NO_MORE_FILES）必须在 CloseHandle 之前读出来：CloseHandle 成功时动不动 last error 没有保证"""
    real_windll, real_get_last_error = ctypes.WinDLL, ctypes.get_last_error
    closed, closed_when_read = [], []

    def traced_windll(*args, **kwargs):
        dll = real_windll(*args, **kwargs)
        real_close = dll.CloseHandle

        def close(handle):
            closed.append(handle)
            return real_close(handle)

        dll.CloseHandle = close
        return dll

    def traced_get_last_error():
        closed_when_read.append(len(closed))   # 读 last error 的那一刻，CloseHandle 已经被调用过几次
        return real_get_last_error()

    monkeypatch.setattr(ctypes, "WinDLL", traced_windll)
    monkeypatch.setattr(ctypes, "get_last_error", traced_get_last_error)

    assert process_utils._snapshot_toolhelp()   # 快照完整，返回非空列表

    assert closed_when_read == [0], "last error 应该在 CloseHandle 之前读"
    assert len(closed) == 1


def test_real_system_dry_run_is_read_only():
    guarded = _claude_with_alive_parent()  # 含正在跑的线上会话
    recorder = _Recorder()

    records = reclaim_orphan_processes(log=recorder, dry_run=True)

    assert isinstance(records, list)
    for record in records:
        assert set(record) == {"pid", "ppid", "create_time", "session_id", "descendants", "killed"}
        assert record["pid"] != os.getpid()
        assert record["killed"] is False
        assert record["pid"] not in guarded, "父进程还活着的 claude.exe 不该被当成孤儿"
    # 护栏：dry-run 之后，父进程存活的 claude.exe 原样还在
    for pid in guarded:
        assert psutil.pid_exists(pid), f"PID={pid} 的线上 claude.exe 被 dry-run 动到了"
    # 日志里不输出命令行/prompt 原文（命令行特征串一旦出现在日志里就说明把 cmdline 打出来了）
    assert all("--output-format stream-json" not in line for line in recorder.lines)


# ========== 5. terminate_process_tree（session_worker 模块级函数） ==========

def _start_async_dummy(*args):
    return asyncio.create_subprocess_exec(
        sys.executable, *map(str, args),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )


@needs_windows
def test_terminate_process_tree_kills_main_and_grandchild(dummy_dir):
    marker = _marker("TREE_TEST")
    grandchild = {}

    async def scenario():
        proc = await _start_async_dummy(dummy_dir / "parent.py", dummy_dir / "leaf.py", marker)
        grandchild["pid"] = int((await asyncio.wait_for(proc.stdout.readline(), timeout=60)).decode().strip())
        recorder = _Recorder()

        result = await session_worker.terminate_process_tree(
            proc, log=recorder, verify={"name": "python.exe", "cmdline_contains": [marker]})

        assert result == "tree"
        assert proc.returncode is not None
        return recorder.lines

    lines = asyncio.run(scenario())
    assert grandchild["pid"] != os.getpid()
    assert _wait_until(lambda: not _marked_pids().get(marker)), "主进程或孙进程没退出"
    assert any(line.startswith("🌳 已终止进程树 PID=") for line in lines)


@needs_windows
def test_terminate_process_tree_on_already_exited_process():
    marker = _marker("EXITED_TEST")

    async def scenario():
        # 已经 await wait() 过：returncode 已知
        exited = await _start_async_dummy("-c", "pass", marker)
        await exited.wait()
        assert await session_worker.terminate_process_tree(exited) == "exited"

        # 进程已经死了但事件循环还没观察到退出（returncode 仍是 None）；verify 带 marker，PID 被复用也不会误杀
        dying = await _start_async_dummy("-c", "pass", marker)
        while psutil.pid_exists(dying.pid):
            await asyncio.sleep(0.05)
        assert await session_worker.terminate_process_tree(
            dying, log=_Recorder(), verify={"name": "python.exe", "cmdline_contains": [marker]}) == "exited"
        assert await asyncio.wait_for(dying.wait(), timeout=60) is not None

    asyncio.run(scenario())


@needs_windows
def test_terminate_process_tree_falls_back_when_tree_kill_fails(dummy_dir, monkeypatch):
    """杀树失败/出错 → 回退为原有的 terminate 链，只终止主进程（行为与改动前一致）"""
    leaf = dummy_dir / "leaf.py"
    seen_verify = []

    async def run_case(fake_kill, expect_log):
        marker = _marker("FALLBACK_TEST")
        proc = await _start_async_dummy(leaf, marker, 1)   # 它会再拉一个带 marker 的子进程
        assert await _wait_async(lambda: len(_marked_pids().get(marker, [])) >= 2), "子进程没起来"
        recorder = _Recorder()

        def fake(pid, verify=None):
            seen_verify.append(verify)
            return fake_kill(pid)

        monkeypatch.setattr(session_worker, "kill_process_tree_detail", fake)
        result = await session_worker.terminate_process_tree(
            proc, log=recorder, verify={"name": "python.exe", "cmdline_contains": [marker]})
        assert result == "terminated" and proc.returncode is not None
        assert expect_log in recorder.lines, recorder.lines
        return marker

    def failing(pid):
        return ("failed", 0)

    def raising(pid):
        raise RuntimeError("boom")

    marker = asyncio.run(run_case(failing, "⚠️ 进程树终止失败，已回退为只终止主进程"))
    second_marker = asyncio.run(run_case(raising, "⚠️ 进程树终止出错，已回退为只终止主进程: boom"))
    # verify 原样传给了杀树函数
    assert seen_verify == [
        {"name": "python.exe", "cmdline_contains": [marker]},
        {"name": "python.exe", "cmdline_contains": [second_marker]},
    ]
    # 回退路径只杀主进程：带 marker 的子进程还留着，由 fixture 兜底清理


@needs_windows
def test_terminate_process_tree_forces_kill_when_terminate_is_ignored(dummy_dir, monkeypatch):
    marker = _marker("FORCED_TEST")

    async def scenario():
        proc = await _start_async_dummy(dummy_dir / "leaf.py", marker, 0)
        _wait_until(lambda: marker in _marked_pids())
        monkeypatch.setattr(session_worker, "kill_process_tree_detail", lambda pid, verify=None: ("failed", 0))
        monkeypatch.setattr(proc, "terminate", lambda: None)   # 模拟 terminate 无效
        recorder = _Recorder()

        result = await session_worker.terminate_process_tree(proc, log=recorder)

        assert result == "forced" and proc.returncode is not None

    asyncio.run(scenario())


@needs_windows
def test_terminate_process_tree_treats_partial_kill_as_tree(dummy_dir, monkeypatch):
    """目标是当前进程的祖先（中断路径上不会出现，这里只保证逻辑完整）：killed_partial 和 killed 同等对待，同样算 "tree" """
    marker = _marker("PARTIAL_TEST")
    monkeypatch.setattr(session_worker, "kill_process_tree_detail",
                        lambda pid, verify=None: (psutil.Process(pid).kill(), ("killed_partial", 2))[1])

    async def scenario():
        proc = await _start_async_dummy(dummy_dir / "leaf.py", marker, 0)
        _wait_until(lambda: marker in _marked_pids())
        recorder = _Recorder()

        result = await session_worker.terminate_process_tree(proc, log=recorder)

        assert result == "tree" and proc.returncode is not None
        return proc.pid, recorder.lines

    pid, lines = asyncio.run(scenario())
    assert lines == [f"🌳 已终止进程树 PID={pid}（连带 2 个子进程）"]


# ========== 5b. 异常收场善后：cleanup_leftover_process ==========

@needs_windows
def test_cleanup_leftover_process_kills_whole_tree(dummy_dir):
    marker = _marker("CLEANUP_TEST")

    async def scenario():
        proc = await _start_async_dummy(dummy_dir / "parent.py", dummy_dir / "leaf.py", marker)
        grandchild_pid = int((await asyncio.wait_for(proc.stdout.readline(), timeout=60)).decode().strip())
        recorder = _Recorder()

        result = await session_worker.cleanup_leftover_process(
            proc, verify={"name": "python.exe", "cmdline_contains": [marker]}, log=recorder)

        assert result == "tree" and proc.returncode is not None
        return grandchild_pid, recorder.lines

    grandchild_pid, lines = asyncio.run(scenario())
    assert _wait_until(lambda: not _marked_pids().get(marker)), "主进程或孙进程没被收掉"
    assert any(line.startswith("🌳 已终止进程树 PID=") for line in lines)
    assert grandchild_pid != os.getpid()


@needs_windows
def test_cleanup_leftover_process_on_exited_process_returns_immediately():
    """进程已经退出（比如返回错误码 1 之后才抛的异常）：立即返回、不抛异常，也不多等 5 秒"""
    marker = _marker("CLEANUP_EXITED_TEST")

    async def timed_cleanup(process, verify=None):
        started = time.monotonic()
        result = await session_worker.cleanup_leftover_process(process, verify=verify, log=_Recorder())
        assert result == "exited"
        assert time.monotonic() - started < 2.0, "进程已经退出还在等，会拖慢每一次失败重试"

    async def scenario():
        # 已经 await wait() 过：returncode 已知
        exited = await _start_async_dummy("-c", "pass", marker)
        await exited.wait()
        await timed_cleanup(exited)

        # 进程已经死了但事件循环还没观察到退出（returncode 仍是 None）：杀树函数报 gone，同样不等待
        dying = await _start_async_dummy("-c", "pass", marker)
        while psutil.pid_exists(dying.pid):
            await asyncio.sleep(0.05)
        await timed_cleanup(dying, verify={"name": "python.exe", "cmdline_contains": [marker]})
        assert await asyncio.wait_for(dying.wait(), timeout=60) is not None

    asyncio.run(scenario())


@needs_windows
def test_cleanup_leftover_process_never_raises(dummy_dir, monkeypatch):
    """善后步骤自己出错也不能往外抛（会掩盖原来的异常）"""
    marker = _marker("CLEANUP_RAISE_TEST")
    recorder = _Recorder()

    async def boom(process, log=None, verify=None):
        raise RuntimeError("boom")

    async def scenario():
        proc = await _start_async_dummy(dummy_dir / "leaf.py", marker, 0)
        _wait_until(lambda: marker in _marked_pids())
        monkeypatch.setattr(session_worker, "terminate_process_tree", boom)
        assert await session_worker.cleanup_leftover_process(proc, log=recorder) is None
        proc.kill()
        await proc.wait()   # 没能善后的进程由测试自己收掉

    asyncio.run(scenario())
    assert recorder.lines == ["⚠️ 清理残留进程出错（忽略）: boom"]


# ========== 5c. _call_claude_cli 的异常分支：重试之前先把还活着的进程收掉 ==========

def _run_call_cli(monkeypatch, marker, *fake_claude_args, claude_timeout, cwd, max_attempts=1):
    """用真实的 SessionWorker._call_claude_cli 跑一次，把"claude 可执行文件"换成一个 dummy python 进程

    create_subprocess_exec 包一层：命令行最前面插上 dummy 脚本及其参数，bridge 拼的参数（--output-format stream-json 等，
    杀树的身份校验要看它们）原样保留在后面。日志换成 Recorder，不写线上日志文件。
    返回 (最终抛出的异常文本, 日志行)。
    """
    recorder = _Recorder()
    real_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def fake_spawn(executable, *args, **kwargs):
        assert executable == sys.executable and "--output-format" in args and "stream-json" in args
        process = await real_spawn(sys.executable, *map(str, fake_claude_args), marker, *args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    async def scenario():
        config = types.SimpleNamespace(max_attempts=max_attempts, claude_timeout=claude_timeout,
                                       claude_executable=sys.executable, working_directory=cwd)
        worker = session_worker.SessionWorker("orphan-reclaim-test", config, types.SimpleNamespace())
        worker._log = recorder
        with pytest.raises(Exception) as excinfo:
            await worker._call_claude_cli(prompt="请看看这个文件", session_key=None, session_id=SESSION_ID,
                                          session_created=True, working_dir=cwd)
        message = str(excinfo.value)
        # _call_claude_cli 没收掉的进程（比如善后被模拟成出错的那种）由测试自己收掉，不留到事件循环关闭之后
        for process in spawned:
            if process.returncode is None:
                process.kill()
            await process.wait()
        return message

    message = asyncio.run(scenario())
    return message, recorder.lines


@needs_windows
def test_call_claude_cli_reclaims_process_on_startup_timeout(dummy_dir, monkeypatch, tmp_path):
    """启动超时（claude 迟迟不发 init）：进重试分支之前，先把还活着的进程连同子进程树收掉；原来的异常原样保留"""
    marker = _marker("STARTUP_TIMEOUT_TEST")

    message, lines = _run_call_cli(monkeypatch, marker, dummy_dir / "silent.py", dummy_dir / "leaf.py",
                                   claude_timeout=0.5, cwd=str(tmp_path))

    assert message == "经过 1 次调用尝试后仍然失败: Claude Code 启动超时（超过 0.5 秒）"
    assert _wait_until(lambda: not _marked_pids().get(marker)), "启动超时的进程及其子进程没有被收掉"
    assert "❌ 调用失败 (尝试 1/1): Claude Code 启动超时（超过 0.5 秒）" in lines
    assert sum(line.startswith("🌳 已终止进程树 PID=") for line in lines) == 1


@needs_windows
def test_call_claude_cli_reclaims_on_every_retry(dummy_dir, monkeypatch, tmp_path):
    """善后不改变重试逻辑：每次尝试的残留进程都收掉，重试次数、重试间隔照旧"""
    marker = _marker("RETRY_TEST")

    message, lines = _run_call_cli(monkeypatch, marker, dummy_dir / "silent.py", dummy_dir / "leaf.py",
                                   claude_timeout=0.5, cwd=str(tmp_path), max_attempts=2)

    assert message == "经过 2 次调用尝试后仍然失败: Claude Code 启动超时（超过 0.5 秒）"
    assert "❌ 调用失败 (尝试 1/2): Claude Code 启动超时（超过 0.5 秒）" in lines
    assert "⏳ 2 秒后重试..." in lines
    assert "❌ 调用失败 (尝试 2/2): Claude Code 启动超时（超过 0.5 秒）" in lines
    assert sum(line.startswith("🌳 已终止进程树 PID=") for line in lines) == 2
    assert _wait_until(lambda: not _marked_pids().get(marker)), "有残留进程没被收掉"


@needs_windows
def test_call_claude_cli_skips_cleanup_when_process_already_exited(dummy_dir, monkeypatch, tmp_path):
    """进程已经退出之后才抛的异常（返回错误码 1）：不走善后（会白等），异常原样往外抛"""
    marker = _marker("EXIT_CODE_TEST")
    cleanup_calls = []
    real_cleanup = session_worker.cleanup_leftover_process

    async def spy_cleanup(process, verify=None, log=None):
        cleanup_calls.append(process)
        return await real_cleanup(process, verify=verify, log=log)

    monkeypatch.setattr(session_worker, "cleanup_leftover_process", spy_cleanup)

    message, lines = _run_call_cli(monkeypatch, marker, "-c", "import sys; sys.exit(1)",
                                   claude_timeout=30, cwd=str(tmp_path))

    assert message == "经过 1 次调用尝试后仍然失败: Claude Code 返回错误码 1"
    assert cleanup_calls == [], "进程已经退出就不该走善后"
    assert not any(line.startswith("🌳") for line in lines)


@needs_windows
def test_call_claude_cli_cleanup_error_does_not_mask_original_error(dummy_dir, monkeypatch, tmp_path):
    """善后步骤出错：只记一条日志，往外抛的仍然是原来那个异常"""
    marker = _marker("CLEANUP_MASK_TEST")

    async def boom(process, log=None, verify=None):
        raise RuntimeError("cleanup boom")

    monkeypatch.setattr(session_worker, "terminate_process_tree", boom)

    message, lines = _run_call_cli(monkeypatch, marker, dummy_dir / "silent.py", dummy_dir / "leaf.py",
                                   claude_timeout=0.5, cwd=str(tmp_path))

    assert message == "经过 1 次调用尝试后仍然失败: Claude Code 启动超时（超过 0.5 秒）"
    assert "⚠️ 清理残留进程出错（忽略）: cleanup boom" in lines
    assert "❌ 调用失败 (尝试 1/1): Claude Code 启动超时（超过 0.5 秒）" in lines


# ========== 6. scripts/kill_bridge.py ==========

@pytest.fixture
def kill_bridge(monkeypatch):
    """按文件路径加载 scripts/kill_bridge.py；加载期间把 subprocess.run 换成桩，确保 import 不会触发 main()"""
    calls = []

    def guard(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", guard)
    spec = importlib.util.spec_from_file_location(
        "kill_bridge_under_test", PROJECT_ROOT / "scripts" / "kill_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert calls == [], "import kill_bridge.py 不该触发 main()"
    assert callable(module.reclaim_claude_children)
    return module


def _stub_subprocess(monkeypatch, kill_bridge, wmic_lines, failing=()):
    """替换 kill_bridge 里的 subprocess：wmic 返回伪造的进程列表，taskkill 按 failing 决定成败"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "wmic":
            return types.SimpleNamespace(returncode=0, stdout="\n".join(wmic_lines))
        return types.SimpleNamespace(returncode=128 if int(cmd[-1]) in failing else 0)

    monkeypatch.setattr(kill_bridge, "subprocess", types.SimpleNamespace(run=fake_run))
    return calls


def test_reclaim_claude_children_only_targets_children_of_killed(kill_bridge, monkeypatch):
    kills = []

    def fake_kill(pid, verify=None, all_procs=None):
        kills.append((pid, verify))
        return ("killed", 2)

    monkeypatch.setattr(kill_bridge, "kill_process_tree_detail", fake_kill)
    procs = [
        _fake(4101, 4100, 200.0),                              # 父进程 4100 刚被我们杀掉
        _fake(4111, 4101, 210.0, name="bash.exe", cmdline="bash -c sleep 600"),   # 4101 的子进程
        _fake(4102, 4200, 200.0),                              # 父进程没被我们杀
        _fake(4103, 4100, 200.0, name="python.exe"),           # 名字不对
        _fake(4105, 4100, 200.0, name="CLAUDE.EXE"),           # 名字不区分大小写
    ]

    # dry_run：只挑出来，不调杀，但后代数要如实报
    records = kill_bridge.reclaim_claude_children([4100], procs=procs, dry_run=True)
    assert [(r["pid"], r["descendants"], r["killed"]) for r in records] == [(4101, 1, False), (4105, 0, False)]
    assert kills == []

    records = kill_bridge.reclaim_claude_children([4100], procs=procs)
    assert [pid for pid, _ in kills] == [4101, 4105]
    assert all(verify == {"name": CLAUDE_PROCESS_NAME, "cmdline_contains": ["--output-format stream-json"]}
               for _, verify in kills)
    assert [(r["pid"], r["killed"], r["descendants"], r["session_id"]) for r in records] == [
        (4101, True, 2, SESSION_ID), (4105, True, 2, SESSION_ID)]


def test_reclaim_claude_children_edge_cases(kill_bridge, monkeypatch):
    kills = []
    monkeypatch.setattr(kill_bridge, "kill_process_tree_detail",
                        lambda pid, verify=None, all_procs=None: kills.append(pid) or ("failed", 0))
    procs = [_fake(4101, 4100, 200.0), _fake(4102, 4100, 200.0)]

    # 没有被杀的 pid / 列表为空 → 什么都不做
    assert kill_bridge.reclaim_claude_children([], procs=procs) == []
    assert kill_bridge.reclaim_claude_children([4100], procs=[]) == []
    assert kills == []

    # 杀失败：如实记录，不抛异常
    assert [(r["pid"], r["killed"]) for r in kill_bridge.reclaim_claude_children([4100], procs=procs)] == [
        (4101, False), (4102, False)]


def test_reclaim_claude_children_records_ancestor_partial_kill_as_success(kill_bridge, monkeypatch, capsys):
    """claude.exe 是当前进程的祖先（restart.bat 是从这个会话里拉起来的）：不再跳过，剪枝回收同样记成功并说明保留了链路"""
    kills = []

    def fake_kill(pid, verify=None, all_procs=None):
        kills.append(pid)
        return ("killed_partial", 1) if pid == 4101 else ("killed", 0)

    monkeypatch.setattr(kill_bridge, "kill_process_tree_detail", fake_kill)
    orphan = _fake(4101, 4100, 100.0)
    me = {"pid": os.getpid(), "ppid": 4101, "name": "python.exe", "create_time": 200.0, "cmdline": "kill_bridge.py"}
    other = _fake(4102, 4100, 150.0)

    records = kill_bridge.reclaim_claude_children([4100], procs=[orphan, me, other])

    assert kills == [4101, 4102], "是祖先也不该跳过"
    assert [(r["pid"], r["killed"], r["descendants"]) for r in records] == [(4101, True, 1), (4102, True, 0)]
    out = capsys.readouterr().out
    assert (f"[reclaim] 已回收 claude.exe 进程树 PID=4101（会话 {SESSION_ID}；它是当前进程的祖先，"
            f"只终止主进程及其余 1 个后代，保留通往当前进程的链路）") in out
    assert f"[reclaim] 已回收 claude.exe 进程树 PID=4102（会话 {SESSION_ID}，连带 0 个子进程）" in out
    assert "跳过" not in out


def test_reclaim_claude_children_swallows_errors(kill_bridge, monkeypatch):
    def boom(pid, verify=None, all_procs=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(kill_bridge, "kill_process_tree_detail", boom)
    assert kill_bridge.reclaim_claude_children([4100], procs=[_fake(4101, 4100, 200.0)]) == []


def test_reclaim_claude_children_skips_when_psutil_unavailable(kill_bridge, monkeypatch):
    """跑脚本的 python 没装 psutil：回收步骤直接跳过，连进程列表都不去枚举"""
    def must_not_enumerate(*args, **kwargs):
        raise AssertionError("psutil 不可用时不应枚举进程")

    monkeypatch.setattr(kill_bridge, "_RECLAIM_AVAILABLE", False)
    monkeypatch.setattr(kill_bridge, "_RECLAIM_ERROR", ModuleNotFoundError("No module named 'psutil'"))
    monkeypatch.setattr(kill_bridge, "list_processes", must_not_enumerate)
    assert kill_bridge.reclaim_claude_children([4100]) == []


def test_kill_bridge_import_survives_missing_psutil(monkeypatch):
    """restart.bat 用裸 python 跑这个脚本，解析到的解释器不一定装了 psutil：
    import 不能在杀进程之前就崩掉（否则旧进程杀不掉、新进程照起）"""
    calls = []

    def guard(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", guard)
    monkeypatch.setitem(sys.modules, "psutil", None)                       # 让 import psutil 直接失败
    monkeypatch.delitem(sys.modules, "shared.process_utils")               # 强制重新导入 shared.process_utils

    spec = importlib.util.spec_from_file_location(
        "kill_bridge_without_psutil", PROJECT_ROOT / "scripts" / "kill_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)                                       # 不抛异常

    assert module._RECLAIM_AVAILABLE is False
    assert isinstance(module._RECLAIM_ERROR, ImportError)
    assert module.reclaim_claude_children([4100]) == []
    assert callable(module.main) and calls == []


@needs_windows
def test_reclaim_claude_children_real_dry_run(kill_bridge):
    """在真实进程列表上 dry-run：不抛异常、不杀任何进程"""
    live = _claude_with_alive_parent()
    records = kill_bridge.reclaim_claude_children([os.getpid()], dry_run=True)
    assert isinstance(records, list)
    assert all(r["pid"] not in live and r["killed"] is False for r in records)
    for pid in live:
        assert psutil.pid_exists(pid)


def test_kill_bridge_main_records_successful_kills_and_reclaims(kill_bridge, monkeypatch):
    lines = [
        "Node,CommandLine,ProcessId",
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\bridge\\claude_bridge.py",4100',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\bot\\discord\\discord_bot.py",4104',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\im_claude_bridge_manager.py",4108',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\web\\web_server.py",4112',
        'PC,"C:\\Python\\pythonw.exe  "D:\\other\\app.py",4116',
        'PC,"C:\\Python\\python.exe  "D:\\x\\IM-claude-bridge\\scripts\\kill_bridge.py" im_claude_bridge_manager web_server",4120',
    ]
    calls = _stub_subprocess(monkeypatch, kill_bridge, lines, failing={4104})
    reclaimed = []
    monkeypatch.setattr(kill_bridge, "reclaim_claude_children",
                        lambda pids: reclaimed.append(list(pids)) or [])
    monkeypatch.setattr(sys, "argv", ["kill_bridge.py", "im_claude_bridge_manager", "web_server"])

    kill_bridge.main()

    taskkills = [cmd for cmd in calls if cmd[0] == "taskkill"]
    assert [int(cmd[-1]) for cmd in taskkills] == [4100, 4104]   # exclude 参数的行为不变
    assert all("/T" not in cmd for cmd in taskkills)            # 不能对 python/pythonw 用 /T
    assert reclaimed == [[4100]]                                 # 只记 taskkill 返回码为 0 的


def test_kill_bridge_main_skips_own_pid(kill_bridge, monkeypatch):
    """stop.bat 不带 exclude 参数：脚本自己的命令行里也有 IM-claude-bridge，不能在循环中间把自己杀了"""
    lines = [
        "Node,CommandLine,ProcessId",
        f'PC,"C:\\Python\\python.exe  "D:\\x\\IM-claude-bridge\\scripts\\kill_bridge.py",{os.getpid()}',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\bridge\\claude_bridge.py",4100',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\bot\\discord\\discord_bot.py",4104',
        'PC,"C:\\Python\\pythonw.exe  "D:\\x\\IM-claude-bridge\\mcp_server\\server.py",4108',
        'PC,"D:\\other\\app.py",4112',
    ]
    calls = _stub_subprocess(monkeypatch, kill_bridge, lines)
    reclaimed = []
    monkeypatch.setattr(kill_bridge, "reclaim_claude_children",
                        lambda pids: reclaimed.append(list(pids)) or [])
    monkeypatch.setattr(sys, "argv", ["kill_bridge.py"])

    kill_bridge.main()

    taskkills = [int(cmd[-1]) for cmd in calls if cmd[0] == "taskkill"]
    assert os.getpid() not in taskkills, "不能 taskkill 自己"
    assert taskkills == [4100, 4104, 4108], "自己的那一行排在最前面：后面的进程也都要杀到"
    assert reclaimed == [[4100, 4104, 4108]], "回收步骤要执行到"


def test_kill_bridge_main_swallows_reclaim_errors(kill_bridge, monkeypatch):
    def boom(pids):
        raise RuntimeError("boom")

    _stub_subprocess(monkeypatch, kill_bridge, ["Node,CommandLine,ProcessId"])
    monkeypatch.setattr(kill_bridge, "reclaim_claude_children", boom)
    monkeypatch.setattr(sys, "argv", ["kill_bridge.py"])

    kill_bridge.main()   # 不抛异常
