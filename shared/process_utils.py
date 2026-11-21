"""
进程工具：进程枚举、孤儿判定、杀进程树、孤儿回收

桥接服务每条消息起一个 claude.exe，它下面还挂着 MCP 服务、Bash 工具的 bash 等子进程。
bridge 被杀或中断时如果只处理 claude.exe 本体，会留下孤儿进程和漏网的后代，这里统一按整棵进程树处理。
"""
import ctypes
import os
import re
import subprocess
import sys
import time
from collections import deque
from ctypes import wintypes
from datetime import datetime

import psutil

# 桥接服务拉起的 claude.exe：进程名 + 命令行特征
CLAUDE_PROCESS_NAME = "claude.exe"
CLAUDE_SIGNATURE = ("--output-format stream-json",)

# session_id：--session-id <uuid> 或 -r <uuid>
SESSION_ID_RE = re.compile(
    r"(?:--session-id\s+|-r\s+)"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

# 杀树结果
KILL_TREE_KILLED = "killed"                  # 根进程已死（整棵树都处理过）
KILL_TREE_KILLED_PARTIAL = "killed_partial"  # 根进程已死，但通往当前进程的那条链路（它是当前进程的祖先）原样保留
KILL_TREE_GONE = "gone"                      # 进程本来就不存在
KILL_TREE_FAILED = "failed"                  # 根进程没杀掉：身份校验不通过 / 非 Windows / 出错 / 补杀后仍活着

_PSUTIL_ERRORS = (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError)

_TH32CS_SNAPPROCESS = 0x00000002
_ERROR_NO_MORE_FILES = 18
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    """PROCESSENTRY32W（Toolhelp32 进程快照里的一行）"""
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _entry(pid, ppid, name, create_time=None, cmdline=""):
    return {
        "pid": int(pid),
        "ppid": int(ppid or 0),
        "name": name or "",
        "create_time": create_time,
        "cmdline": cmdline or "",
    }


def _snapshot_toolhelp():
    """一次系统调用拿到全部进程的 pid/ppid/name（约 10ms）；不可用或快照不完整时返回 None"""
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

        handle = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not handle or handle == _INVALID_HANDLE_VALUE:
            return None
        procs = []
        last_error = None
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            more = k32.Process32FirstW(handle, ctypes.byref(entry))
            while more:
                procs.append(_entry(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
                more = k32.Process32NextW(handle, ctypes.byref(entry))
            # 紧跟着失败的 Process32NextW 读：放到 CloseHandle 之后再读会受它影响（成功时动不动 last error 没有保证）
            last_error = ctypes.get_last_error()
        finally:
            k32.CloseHandle(handle)
        if last_error != _ERROR_NO_MORE_FILES:
            return None  # 中途出错，快照不完整（漏掉父进程会被误判成孤儿）
        return procs or None
    except Exception:
        return None


def _snapshot_psutil():
    """psutil 逐个枚举（Toolhelp 快照不可用时的兜底）；单个进程出错不影响整体"""
    procs = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
        try:
            info = proc.info
            try:
                cmdline = " ".join(proc.cmdline())
            except _PSUTIL_ERRORS:
                cmdline = ""
            procs.append(_entry(info["pid"], info["ppid"], info["name"], info["create_time"], cmdline))
        except Exception:
            continue
    return procs


def list_processes() -> list[dict]:
    """枚举全部进程 → [{"pid": int, "ppid": int, "name": str, "create_time": float|None, "cmdline": str}]

    pid/ppid/name 取自一次系统快照；create_time/cmdline 得逐个查询（这台机器上全量要 10 秒以上），
    所以先留 None / ""，由用到它们的函数经 fill_process_detail 按需补全，查询不到就保持 None / ""。
    """
    procs = _snapshot_toolhelp()
    if procs is None:
        procs = _snapshot_psutil()
    return procs


def fill_process_detail(proc: dict, cmdline: bool = False) -> dict:
    """按需补全 create_time（以及 cmdline），原地修改并返回"""
    try:
        target = psutil.Process(proc["pid"])
        if proc.get("create_time") is None:
            proc["create_time"] = target.create_time()
        if cmdline and not proc.get("cmdline"):
            proc["cmdline"] = " ".join(target.cmdline())
    except _PSUTIL_ERRORS:
        pass
    return proc


def _create_time(proc: dict):
    return fill_process_detail(proc).get("create_time")


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except _PSUTIL_ERRORS:
        return True  # 查不了（权限不足）按存活处理


def _parent_alive(parent: dict, child: dict) -> bool:
    """父进程比子进程先创建才算存活（比子进程晚创建 = PID 被复用，真正的父进程已死）；create_time 取不到按存活处理"""
    parent_ct, child_ct = _create_time(parent), _create_time(child)
    if parent_ct is None or child_ct is None:
        return True
    return parent_ct <= child_ct


def find_orphan_processes(process_name, signature_patterns=(), all_procs=None, exclude_pids=()) -> list[dict]:
    """找出父进程已不存活的进程：name 相符（不区分大小写）、cmdline 包含 signature_patterns 的每一项

    "父进程存活" = 列表里存在 pid == ppid 的进程，且它比子进程先创建。
    """
    procs = list_processes() if all_procs is None else all_procs
    by_pid = {p["pid"]: p for p in procs}
    want_name = process_name.lower()
    excluded = set(exclude_pids) | {os.getpid()}
    orphans = []
    for proc in procs:
        if (proc.get("name") or "").lower() != want_name or proc["pid"] in excluded:
            continue
        if signature_patterns:
            fill_process_detail(proc, cmdline=True)
            if not all(s in proc["cmdline"] for s in signature_patterns):
                continue
        parent = by_pid.get(proc["ppid"])
        if parent is not None and _parent_alive(parent, proc):
            continue
        orphans.append(proc)
    return orphans


def get_ancestor_pids(all_procs=None) -> set:
    """当前进程的祖先 pid 集合（顺着 ppid 往上走，遇到断链或 PID 被复用的环节为止）"""
    procs = list_processes() if all_procs is None else all_procs
    by_pid = {p["pid"]: p for p in procs}
    ancestors = set()
    node = by_pid.get(os.getpid())
    while node is not None:
        parent = by_pid.get(node.get("ppid"))
        if parent is None or parent["pid"] in ancestors:
            break
        parent_ct, node_ct = _create_time(parent), _create_time(node)
        if parent_ct is not None and node_ct is not None and parent_ct > node_ct:
            break
        ancestors.add(parent["pid"])
        node = parent
    return ancestors


def _descendants(pid, procs, root_create_time=None, prune=frozenset()):
    """按 ppid 广度优先收集 pid 的全部后代（由浅到深）

    返回 (后代列表, 是否存在陈旧链接)。比父进程还早创建的"子进程"不算后代：
    那是父进程的 PID 以前被别的进程用过、留下的假链接，taskkill /T 会顺着它误杀无关进程。
    prune 里的 pid 一旦作为子进程出现就整枝剪掉：它和它的全部后代都不进结果。
    """
    children = {}
    for proc in procs:
        children.setdefault(proc.get("ppid"), []).append(proc)
    root = next((p for p in procs if p["pid"] == pid), None)
    root_ct = root_create_time if root_create_time is not None else (_create_time(root) if root else None)

    descendants, stale = [], False
    queue, seen = deque([(pid, root_ct)]), {pid}
    while queue:
        cur, cur_ct = queue.popleft()
        for child in children.get(cur, []):
            if child["pid"] in seen or child["pid"] in prune:
                continue
            child_ct = _create_time(child)
            if cur_ct is not None and child_ct is not None and child_ct < cur_ct:
                stale = True
                continue
            seen.add(child["pid"])
            descendants.append(child)
            queue.append((child["pid"], child_ct))
    return descendants, stale


def get_descendants(pid, all_procs=None) -> list[dict]:
    """按 ppid 递归收集 pid 的所有后代（由浅到深；陈旧的 ppid 链接不算）"""
    procs = list_processes() if all_procs is None else all_procs
    return _descendants(pid, procs)[0]


def extract_session_id(cmdline) -> str:
    """从命令行提取 session_id（--session-id <uuid> 或 -r <uuid>），取不到给 """""
    match = SESSION_ID_RE.search(cmdline or "")
    return match.group(1) if match else ""


def _verify(target: "psutil.Process", verify) -> bool:
    """身份校验：name 相符（不区分大小写）且 cmdline 包含所有给定子串；读不到 cmdline 视为不符"""
    if not verify:
        return True
    try:
        expected = (verify.get("name") or "").lower()
        if expected:
            actual = target.name().lower()
            if actual != expected and actual.removesuffix(".exe") != expected.removesuffix(".exe"):
                return False
        needles = verify.get("cmdline_contains") or []
        if needles:
            cmdline = " ".join(target.cmdline())
            return all(needle in cmdline for needle in needles)
        return True
    except Exception:
        return False


def _taskkill_tree(pid):
    """taskkill /F /T：杀 pid 及其子进程（不依赖 PATH 里的 taskkill）；失败不抛，交给后面的 psutil 兜底"""
    try:
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        subprocess.run(
            [taskkill, "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15,
        )
    except Exception:
        pass


def _psutil_kill(pid, create_time=None) -> bool:
    """psutil 补杀一个进程；给了 create_time 就先核对，PID 已被别的进程复用则不杀"""
    try:
        target = psutil.Process(pid)
        if create_time is not None and abs(target.create_time() - create_time) > 1e-3:
            return False
        target.kill()
        return True
    except _PSUTIL_ERRORS:
        return False


def _wait_gone(pids, timeout) -> bool:
    """等这批 pid 全部消失，返回是否都消失了"""
    deadline = time.monotonic() + timeout
    while any(_pid_alive(pid) for pid in pids):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _kill_root_verified(pid, root_ct, verify):
    """杀根进程前再核一次身份（防 PID 被复用）：cmdline 对得上、create_time 还是当初那个才杀"""
    if not _pid_alive(pid):
        return
    try:
        if _verify(psutil.Process(pid), verify):
            _psutil_kill(pid, root_ct)
    except _PSUTIL_ERRORS:
        pass


def _kill_tree_pruned(pid, root_ct, ancestors, procs, verify) -> tuple:
    """目标是当前进程的祖先时的剪枝回收，返回 (KILL_TREE_KILLED_PARTIAL 或 KILL_TREE_FAILED, 实际杀掉的后代数量)

    典型场景：重启是从某个 Claude 会话内部触发的，链路是
        kill_bridge.py / 新 bridge ← cmd.exe(restart.bat) ← MCP 服务 ← claude.exe X（目标，父进程已死）
    这时不能对 X 用 taskkill /T（会顺着链路把正在跑的重启脚本连当前进程一起杀掉）。改成从 X 开始按 ppid
    广度优先遍历，当前进程的祖先（以及当前进程自己，它可能就是 X 的直接子进程）一出现就剪枝：它和它的整棵
    子树都不碰；其余后代从最深一层往上用 psutil 逐个杀，最后重新校验身份再杀 X 本身。X 还活着就是 failed。
    """
    descendants, _ = _descendants(pid, procs, root_ct, prune=ancestors | {os.getpid()})
    for descendant in reversed(descendants):
        _psutil_kill(descendant["pid"], descendant.get("create_time"))
    _kill_root_verified(pid, root_ct, verify)
    _wait_gone([pid] + [d["pid"] for d in descendants], 3.0)
    killed = sum(1 for d in descendants if not _pid_alive(d["pid"]))
    return (KILL_TREE_FAILED if _pid_alive(pid) else KILL_TREE_KILLED_PARTIAL), killed


def kill_process_tree_detail(pid, verify=None, all_procs=None) -> tuple:
    """杀整棵进程树（含所有后代），返回 (结果, 记下的后代数量)

    结果见 KILL_TREE_*。"gone" 表示进程本来就不存在；"failed" 表示没杀掉（不抛异常）。
    目标是当前进程的祖先时不走 taskkill，改为剪枝回收（见 _kill_tree_pruned），
    返回 "killed_partial"，数量是实际杀掉的后代数（被剪掉的那条链路不算）。
    """
    if sys.platform != "win32":
        return KILL_TREE_FAILED, 0
    try:
        target = psutil.Process(pid)
        if not _verify(target, verify):
            return KILL_TREE_FAILED, 0
        root_ct = target.create_time()
    except psutil.NoSuchProcess:
        return KILL_TREE_GONE, 0
    except _PSUTIL_ERRORS:
        return KILL_TREE_FAILED, 0  # 进程在但没权限查
    try:
        procs = list_processes() if all_procs is None else list(all_procs)
        ancestors = get_ancestor_pids(procs)
        if pid in ancestors:
            # 它是当前进程的祖先：/T 会顺着链路把正在跑的重启脚本连自己一起杀掉，只能剪枝回收
            return _kill_tree_pruned(pid, root_ct, ancestors, procs, verify)

        descendants, stale = _descendants(pid, procs, root_ct)
        tree_pids = [pid] + [d["pid"] for d in descendants]
        if stale:
            # taskkill /T 会顺着陈旧链接误杀无关进程：只杀核实过的，先子后根
            for descendant in reversed(descendants):
                _psutil_kill(descendant["pid"], descendant.get("create_time"))
            _psutil_kill(pid, root_ct)
        else:
            _taskkill_tree(pid)
        _wait_gone(tree_pids, 2.0)

        # 兜底：树杀漏了的从最深一层往上逐个补杀，最后确认根进程已死
        for descendant in reversed(descendants):
            if _pid_alive(descendant["pid"]):
                _psutil_kill(descendant["pid"], descendant.get("create_time"))
        _kill_root_verified(pid, root_ct, verify)
        _wait_gone(tree_pids, 3.0)
        return (KILL_TREE_KILLED if not _pid_alive(pid) else KILL_TREE_FAILED), len(descendants)
    except Exception:
        return KILL_TREE_FAILED, 0


def kill_process_tree(pid, verify=None, all_procs=None) -> bool:
    """杀整棵进程树（含所有后代），返回根进程是否已被杀掉

    杀之前按 verify（{"name": ..., "cmdline_contains": [...]}）重新确认身份，对不上就不杀（防 PID 复用误杀）。
    目标是当前进程的祖先时只终止它和其余后代、保留通往当前进程的链路（KILL_TREE_KILLED_PARTIAL），根进程同样算已杀掉。
    非 Windows 或进程已不存在时返回 False，不抛异常。
    """
    return kill_process_tree_detail(pid, verify=verify, all_procs=all_procs)[0] in (
        KILL_TREE_KILLED, KILL_TREE_KILLED_PARTIAL)


def _emit(log, message: str):
    """log 为 None 时 print，否则用 log.log(...)"""
    try:
        if log is None:
            print(message)
        else:
            getattr(log, "log", log)(message)
    except Exception:
        pass


def _fmt_time(create_time) -> str:
    try:
        return datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "未知"


def reclaim_orphan_processes(log=None, process_name=CLAUDE_PROCESS_NAME,
                             signature_patterns=CLAUDE_SIGNATURE, dry_run=False,
                             all_procs=None) -> list[dict]:
    """回收父进程已死的进程（上次运行遗留的 claude.exe），返回处理记录；绝不抛异常

    记录格式：[{"pid", "ppid", "create_time", "session_id", "descendants", "killed"}]
    dry_run=True 时只列不杀。孤儿是当前进程的祖先时（重启是从那个会话里触发的）只终止它和其余后代、
    保留通往当前进程的链路，根进程杀掉了同样记 killed=True。
    """
    label = process_name.lower().removesuffix(".exe")
    try:
        procs = list_processes() if all_procs is None else list(all_procs)
        orphans = find_orphan_processes(process_name, signature_patterns, all_procs=procs)
        if not orphans:
            _emit(log, f"✓ 没有遗留的孤儿 {label} 进程")
            return []
        if dry_run:
            _emit(log, f"♻️ 发现 {len(orphans)} 个孤儿 {label} 进程（dry-run：只列出不回收）")
        else:
            _emit(log, f"♻️ 发现 {len(orphans)} 个上次运行遗留的孤儿 {label} 进程，正在回收...")

        verify = {"name": process_name, "cmdline_contains": list(signature_patterns)}
        records = []
        for proc in orphans:
            pid = proc["pid"]
            fill_process_detail(proc, cmdline=True)
            record = {
                "pid": pid,
                "ppid": proc["ppid"],
                "create_time": proc.get("create_time"),
                "session_id": extract_session_id(proc.get("cmdline")),
                "descendants": len(_descendants(pid, procs)[0]),
                "killed": False,
            }
            session = f"会话 {record['session_id']}" if record["session_id"] else "无会话 ID"
            started = f"启动于 {_fmt_time(record['create_time'])}，{session}"
            if dry_run:
                _emit(log, f"👀 [dry-run] 孤儿 {label} 进程 PID={pid}（{started}，连带 {record['descendants']} 个子进程）")
            else:
                status, count = kill_process_tree_detail(pid, verify=verify, all_procs=procs)
                record["descendants"] = count
                record["killed"] = status in (KILL_TREE_KILLED, KILL_TREE_KILLED_PARTIAL)
                if status == KILL_TREE_KILLED:
                    _emit(log, f"🗑️ 已回收孤儿 {label} 进程 PID={pid}（{started}，连带 {count} 个子进程）")
                elif status == KILL_TREE_KILLED_PARTIAL:
                    _emit(log, f"🗑️ 已回收孤儿 {label} 进程 PID={pid}（{started}；它是当前进程的祖先，"
                               f"只终止主进程及其余 {count} 个后代，保留通往当前进程的链路）")
                elif status == KILL_TREE_GONE:
                    _emit(log, f"✓ 孤儿 {label} 进程 PID={pid} 已自行退出")
                else:
                    _emit(log, f"⚠️ 回收孤儿进程失败 PID={pid}")
            records.append(record)
        return records
    except Exception as e:
        _emit(log, f"⚠️ 回收孤儿进程时出错: {e}")
        return []
