"""Kill all IM-claude-bridge related Python processes.

Usage:
    python kill_bridge.py                         # kill all (for stop.bat)
    python kill_bridge.py X Y ...                 # skip processes matching X, Y, ...
"""
import os
import subprocess
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from shared.process_utils import (
        CLAUDE_PROCESS_NAME,
        CLAUDE_SIGNATURE,
        KILL_TREE_GONE,
        KILL_TREE_KILLED,
        KILL_TREE_KILLED_PARTIAL,
        extract_session_id,
        fill_process_detail,
        get_descendants,
        kill_process_tree_detail,
        list_processes,
    )
    _RECLAIM_AVAILABLE = True
    _RECLAIM_ERROR = None
except Exception as e:  # 跑这个脚本的 python 没装 psutil 等：跳过回收步骤，不影响原有的杀进程逻辑
    _RECLAIM_AVAILABLE = False
    _RECLAIM_ERROR = e


def _say(message):
    """输出在 restart.bat 里被重定向到 nul，打印失败也不能影响退出行为"""
    try:
        print(message)
    except Exception:
        pass


def reclaim_claude_children(killed_pids, procs=None, dry_run=False):
    """父进程刚被我们杀掉的 claude.exe 连同其子进程树一并回收（按 ppid 匹配，不做孤儿判定）

    返回处理记录 [{"pid", "ppid", "session_id", "descendants", "killed"}]；任何异常都吞掉。
    claude.exe 是当前进程的祖先时（restart.bat 是从这个会话里拉起来的）只终止它和其余后代、
    保留通往当前进程的链路，根进程杀掉了同样记 killed=True。
    """
    records = []
    try:
        killed = {int(pid) for pid in (killed_pids or [])}
        if not killed:
            return records
        if procs is None:
            if not _RECLAIM_AVAILABLE:
                _say(f"[reclaim] 跳过：无法加载 shared.process_utils（{_RECLAIM_ERROR}）")
                return records
            procs = list_processes()

        verify = {"name": CLAUDE_PROCESS_NAME, "cmdline_contains": list(CLAUDE_SIGNATURE)}
        for proc in procs:
            if (proc.get("name") or "").lower() != CLAUDE_PROCESS_NAME or proc.get("ppid") not in killed:
                continue
            pid = proc["pid"]
            fill_process_detail(proc, cmdline=True)
            record = {
                "pid": pid,
                "ppid": proc["ppid"],
                "session_id": extract_session_id(proc.get("cmdline")),
                "descendants": len(get_descendants(pid, all_procs=procs)),
                "killed": False,
            }
            if dry_run:
                records.append(record)
                continue
            status, count = kill_process_tree_detail(pid, verify=verify)
            record["descendants"] = count
            record["killed"] = status in (KILL_TREE_KILLED, KILL_TREE_KILLED_PARTIAL)
            session = record["session_id"] or "-"
            if status == KILL_TREE_KILLED:
                _say(f"[reclaim] 已回收 claude.exe 进程树 PID={pid}（会话 {session}，连带 {count} 个子进程）")
            elif status == KILL_TREE_KILLED_PARTIAL:
                _say(f"[reclaim] 已回收 claude.exe 进程树 PID={pid}（会话 {session}；它是当前进程的祖先，"
                     f"只终止主进程及其余 {count} 个后代，保留通往当前进程的链路）")
            elif status == KILL_TREE_GONE:
                _say(f"[reclaim] PID={pid} 已自行退出")
            else:
                _say(f"[reclaim] 回收失败 PID={pid}")
            records.append(record)
    except Exception as e:
        _say(f"[reclaim] 出错（忽略）: {e}")
    return records


def main():
    excludes = sys.argv[1:]
    killed_pids = []

    # wmic /format:csv outputs ASCII with \r\n line endings
    result = subprocess.run(
        ["wmic", "process", "where",
         "name='python.exe' or name='pythonw.exe'",
         "get", "processid,commandline", "/format:csv"],
        capture_output=True, text=True
    )

    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if "IM-claude-bridge" not in line:
            continue
        if any(exc in line for exc in excludes):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[2].isdigit():
            pid = int(parts[2])
            if pid == os.getpid():
                # 跳过自己：stop.bat 不带 exclude 参数，不跳过的话可能轮到一半把自己杀了，后面的进程没人杀、回收步骤也不执行
                continue
            try:
                ret = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True
                )
                if ret.returncode == 0:
                    killed_pids.append(pid)
            except Exception:
                pass

    # 刚被杀掉的 python/pythonw 名下的 claude.exe 连同子进程树顺手回收。
    # 注意：不能对 python/pythonw 本身用 /T —— restart.bat 可能是从 bridge 的进程树里拉起来的，
    # /T 会把正在执行的重启脚本连自己一起杀掉。
    try:
        reclaim_claude_children(killed_pids)
    except Exception:
        pass


if __name__ == "__main__":
    main()
