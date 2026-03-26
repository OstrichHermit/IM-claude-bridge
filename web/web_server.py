"""
IM-Claude-Bridge Web 控制界面
基于 FastAPI 的 Web 服务器，提供日志监控和系统控制功能
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# 日志配置
# ============================================================================

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILES = {
    "discord_bot": LOG_DIR / "discord_bot.log",
    "weixin_bot": LOG_DIR / "weixin_bot.log",
    "claude_bridge": LOG_DIR / "claude_bridge.log",
    "manager": LOG_DIR / "manager.log",
    "web_server": LOG_DIR / "web_server.log",
}


def log(message: str):
    """写入 web_server 日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"
    print(log_line.strip())
    try:
        with open(LOG_FILES["web_server"], "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        print(f"⚠️  写入日志失败: {e}")


# ============================================================================
# FastAPI 应用
# ============================================================================

app = FastAPI(title="IM-Claude-Bridge Control", description="Web 控制界面")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket 连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        self.active_connections[channel].append(websocket)

    def disconnect(self, websocket: WebSocket, channel: str):
        if channel in self.active_connections:
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)

    async def broadcast(self, channel: str, message: str):
        if channel in self.active_connections:
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_text(message)
                except:
                    pass


manager_ws = ConnectionManager()


# ============================================================================
# 进程管理辅助函数
# ============================================================================

_process_cache = {"python": [], "pythonw": [], "cache_time": 0}

def _refresh_process_cache():
    """刷新进程缓存"""
    import time
    now = time.time()
    if _process_cache["cache_time"] and now - _process_cache["cache_time"] < 1:
        return
    try:
        result = subprocess.run(
            ['wmic', 'process', 'get', 'ProcessId,CommandLine', '/format:csv'],
            capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW
        )
        lines = result.stdout.strip().split('\n')
        _process_cache["python"] = [l for l in lines if "python.exe" in l.lower()]
        _process_cache["pythonw"] = [l for l in lines if "pythonw.exe" in l.lower()]
        _process_cache["cache_time"] = now
    except:
        pass

def find_process_by_commandline(pattern: str) -> Optional[int]:
    """通过命令行参数查找进程 PID（支持 python.exe 和 pythonw.exe）"""
    _refresh_process_cache()
    try:
        for line in _process_cache["python"] + _process_cache["pythonw"]:
            if pattern.lower() in line.lower():
                parts = line.split(',')
                if len(parts) >= 3:
                    pid_str = parts[2].strip('"')
                    if pid_str.isdigit():
                        return int(pid_str)
    except Exception as e:
        log(f"⚠️  查找进程失败 ({pattern}): {e}")
    return None


def get_component_status(component: str) -> Dict:
    """获取组件状态"""
    patterns = {
        "discord_bot": "discord_bot.py",
        "weixin_bot": "weixin_bot.py",
        "claude_bridge": "claude_bridge.py",
        "manager": "im_claude_bridge_manager.py",
        "web_server": "web_server.py",
    }
    pattern = patterns.get(component)
    if not pattern:
        return {"running": False, "pid": None, "uptime": 0}

    pid = find_process_by_commandline(pattern)
    return {
        "running": pid is not None,
        "pid": pid,
        "uptime": 0  # 简化处理
    }


def read_last_lines(log_file: Path, lines: int = 100) -> List[str]:
    """读取日志文件最后 N 行"""
    if not log_file.exists():
        return []

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return [line.strip() for line in all_lines[-lines:]]
    except Exception as e:
        return [f"Error reading log: {e}"]


# ============================================================================
# API 端点
# ============================================================================

@app.get("/api/status")
async def get_status():
    """获取所有组件状态"""
    components = ["discord_bot", "weixin_bot", "claude_bridge", "manager", "web_server"]
    status = {}
    for comp in components:
        status[comp] = get_component_status(comp)
    return {"status": status, "timestamp": datetime.now().isoformat()}


@app.get("/api/logs/{component}")
async def get_logs(component: str, lines: int = Query(100, ge=1, le=1000)):
    """获取组件日志"""
    if component not in LOG_FILES:
        raise HTTPException(status_code=404, detail="Component not found")

    log_file = LOG_FILES[component]
    log_lines = read_last_lines(log_file, lines)
    return {
        "component": component,
        "lines": log_lines,
        "count": len(log_lines),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/stats")
async def get_stats():
    """获取消息队列统计"""
    db_path = PROJECT_ROOT / "shared" / "messages.db"
    if not db_path.exists():
        return {"error": "Database not found"}

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # 消息统计
        cursor.execute("SELECT status, COUNT(*) FROM messages GROUP BY status")
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # 会话统计
        cursor.execute("SELECT COUNT(*) FROM sessions")
        session_count = cursor.fetchone()[0]

        conn.close()

        return {
            "messages": status_counts,
            "total_messages": sum(status_counts.values()),
            "active_sessions": session_count,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/control/{action}")
async def control_action(action: str, component: str = "all"):
    """控制系统操作"""
    script_dir = PROJECT_ROOT

    if action == "stop":
        script_path = script_dir / "stop.bat"
        log("🛑 收到停止请求")
    elif action == "restart":
        script_path = script_dir / "restart.bat"
        log("🔄 收到重启请求")
    elif action == "start":
        script_path = script_dir / "start.bat"
        log("🚀 收到启动请求")
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"Script not found: {script_path}")

    try:
        subprocess.Popen(
            ["cmd", "/c", str(script_path)],
            cwd=script_dir,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return {"success": True, "action": action, "message": f"{action.capitalize()} command executed"}
    except Exception as e:
        log(f"❌ 控制命令执行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WebSocket 实时日志
# ============================================================================

@app.websocket("/ws/logs/{component}")
async def websocket_log(websocket: WebSocket, component: str):
    """WebSocket 实时日志流"""
    if component not in LOG_FILES:
        await websocket.close(code=4004)
        return

    await manager_ws.connect(websocket, f"logs_{component}")
    log_file = LOG_FILES[component]

    try:
        # 发送初始连接消息
        await websocket.send_json({"type": "connected", "component": component})

        # 如果日志文件存在，发送最后几行作为初始内容
        if log_file.exists():
            last_lines = read_last_lines(log_file, 50)
            for line in last_lines:
                await websocket.send_json({"type": "log", "data": line})

        # 持续监控新内容
        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                # 跳到文件末尾
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        await websocket.send_json({"type": "log", "data": line.strip()})
                    else:
                        await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        log(f"WebSocket 客户端断开: {component}")
    except Exception as e:
        log(f"WebSocket 错误 ({component}): {e}")
    finally:
        manager_ws.disconnect(websocket, f"logs_{component}")


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """WebSocket 状态推送"""
    await manager_ws.connect(websocket, "status")
    try:
        await websocket.send_json({"type": "connected", "channel": "status"})

        while True:
            components = ["discord_bot", "weixin_bot", "claude_bridge", "manager", "web_server"]
            status = {}
            for comp in components:
                status[comp] = get_component_status(comp)

            await websocket.send_json({
                "type": "status_update",
                "data": status,
                "timestamp": datetime.now().isoformat()
            })
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log(f"Status WebSocket 错误: {e}")
    finally:
        manager_ws.disconnect(websocket, "status")


# ============================================================================
# HTML 页面
# ============================================================================

@app.get("/")
async def get_dashboard():
    """主页仪表盘"""
    html_path = PROJECT_ROOT / "web" / "templates" / "dashboard.html"
    if html_path.exists():
        return FileResponse(str(html_path))

    # 如果模板不存在，返回内联 HTML
    return HTMLResponse(DASHBOARD_HTML)


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IM-Claude-Bridge 控制台</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        h1 { color: #00d4ff; margin-bottom: 20px; font-size: 1.8rem; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .header-info { color: #888; font-size: 0.9rem; }

        /* 状态卡片 */
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .status-card { background: #16213e; border-radius: 10px; padding: 20px; border: 1px solid #0f3460; }
        .status-card h3 { font-size: 0.9rem; color: #888; margin-bottom: 10px; }
        .status-indicator { display: flex; align-items: center; gap: 10px; }
        .status-dot { width: 12px; height: 12px; border-radius: 50%; background: #e74c3c; }
        .status-dot.running { background: #2ecc71; box-shadow: 0 0 10px #2ecc71; }
        .status-text { font-size: 1.2rem; font-weight: bold; }
        .status-pid { font-size: 0.8rem; color: #666; }

        /* 控制按钮 */
        .control-panel { background: #16213e; border-radius: 10px; padding: 20px; margin-bottom: 20px; border: 1px solid #0f3460; }
        .control-panel h2 { font-size: 1rem; margin-bottom: 15px; color: #888; }
        .btn-group { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 25px; border: none; border-radius: 5px; cursor: pointer; font-size: 0.9rem; font-weight: bold; transition: all 0.3s; }
        .btn-start { background: #2ecc71; color: white; }
        .btn-stop { background: #e74c3c; color: white; }
        .btn-restart { background: #f39c12; color: white; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.3); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        /* 标签页 */
        .tabs { display: flex; gap: 5px; margin-bottom: 0; background: #16213e; padding: 10px 10px 0; border-radius: 10px 10px 0 0; border: 1px solid #0f3460; border-bottom: none; }
        .tab { padding: 10px 20px; background: transparent; border: none; color: #888; cursor: pointer; border-radius: 5px 5px 0 0; font-size: 0.85rem; }
        .tab.active { background: #1a1a2e; color: #00d4ff; }
        .tab:hover { color: #00d4ff; }

        /* 日志查看器 */
        .log-viewer { background: #16213e; border-radius: 0 0 10px 10px; padding: 15px; border: 1px solid #0f3460; border-top: none; min-height: 400px; max-height: 60vh; overflow-y: auto; }
        .log-line { font-family: 'Consolas', 'Monaco', monospace; font-size: 0.8rem; padding: 3px 0; border-bottom: 1px solid #0f3460; white-space: pre-wrap; word-break: break-all; }
        .log-line:last-child { border-bottom: none; }
        .log-timestamp { color: #666; margin-right: 10px; }
        .log-viewer::-webkit-scrollbar { width: 8px; }
        .log-viewer::-webkit-scrollbar-track { background: #0f3460; }
        .log-viewer::-webkit-scrollbar-thumb { background: #00d4ff; border-radius: 4px; }

        /* 统计信息 */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #16213e; border-radius: 10px; padding: 15px; text-align: center; border: 1px solid #0f3460; }
        .stat-value { font-size: 2rem; font-weight: bold; color: #00d4ff; }
        .stat-label { font-size: 0.8rem; color: #888; margin-top: 5px; }

        /* 刷新指示器 */
        .refresh-indicator { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #2ecc71; margin-left: 10px; }
        .refresh-indicator.error { background: #e74c3c; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>IM-Claude-Bridge 控制台</h1>
            <div class="header-info">
                <span id="lastUpdate">最后更新: --</span>
                <span id="connectionStatus" class="refresh-indicator"></span>
            </div>
        </div>

        <!-- 状态卡片 -->
        <div class="status-grid" id="statusGrid">
            <div class="status-card" data-component="discord_bot">
                <h3>Discord Bot</h3>
                <div class="status-indicator">
                    <div class="status-dot" id="discord_bot_dot"></div>
                    <span class="status-text" id="discord_bot_status">--</span>
                </div>
                <div class="status-pid" id="discord_bot_pid"></div>
            </div>
            <div class="status-card" data-component="weixin_bot">
                <h3>Weixin Bot</h3>
                <div class="status-indicator">
                    <div class="status-dot" id="weixin_bot_dot"></div>
                    <span class="status-text" id="weixin_bot_status">--</span>
                </div>
                <div class="status-pid" id="weixin_bot_pid"></div>
            </div>
            <div class="status-card" data-component="claude_bridge">
                <h3>Claude Bridge</h3>
                <div class="status-indicator">
                    <div class="status-dot" id="claude_bridge_dot"></div>
                    <span class="status-text" id="claude_bridge_status">--</span>
                </div>
                <div class="status-pid" id="claude_bridge_pid"></div>
            </div>
            <div class="status-card" data-component="manager">
                <h3>Manager</h3>
                <div class="status-indicator">
                    <div class="status-dot" id="manager_dot"></div>
                    <span class="status-text" id="manager_status">--</span>
                </div>
                <div class="status-pid" id="manager_pid"></div>
            </div>
            <div class="status-card" data-component="web_server">
                <h3>Web Server</h3>
                <div class="status-indicator">
                    <div class="status-dot running" id="web_server_dot"></div>
                    <span class="status-text" id="web_server_status">运行中</span>
                </div>
                <div class="status-pid" id="web_server_pid"></div>
            </div>
        </div>

        <!-- 控制面板 -->
        <div class="control-panel">
            <h2>系统控制</h2>
            <div class="btn-group">
                <button class="btn btn-start" onclick="controlAction('start')">🚀 启动全部</button>
                <button class="btn btn-restart" onclick="controlAction('restart')">🔄 重启全部</button>
                <button class="btn btn-stop" onclick="controlAction('stop')">🛑 停止全部</button>
            </div>
        </div>

        <!-- 统计 -->
        <div class="stats-grid" id="statsGrid">
            <div class="stat-card">
                <div class="stat-value" id="statTotal">--</div>
                <div class="stat-label">总消息</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statPending">--</div>
                <div class="stat-label">待处理</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statProcessing">--</div>
                <div class="stat-label">处理中</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statCompleted">--</div>
                <div class="stat-label">已完成</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statFailed">--</div>
                <div class="stat-label">失败</div>
            </div>
        </div>

        <!-- 日志查看器 -->
        <div class="tabs">
            <button class="tab active" onclick="switchTab('manager')">Manager</button>
            <button class="tab" onclick="switchTab('discord_bot')">Discord</button>
            <button class="tab" onclick="switchTab('weixin_bot')">Weixin</button>
            <button class="tab" onclick="switchTab('claude_bridge')">Bridge</button>
        </div>
        <div class="log-viewer" id="logViewer"></div>
    </div>

    <script>
        let currentTab = 'manager';
        let wsLogs = {};
        let wsStatus = null;

        // 初始化
        document.addEventListener('DOMContentLoaded', () => {
            fetchStatus();
            fetchStats();
            connectStatusWS();
            connectLogWS(currentTab);
        });

        // WebSocket: 状态
        function connectStatusWS() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            wsStatus = new WebSocket(`${protocol}//${location.host}/ws/status`);

            wsStatus.onopen = () => {
                document.getElementById('connectionStatus').classList.remove('error');
            };

            wsStatus.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'status_update') {
                    updateStatus(data.data);
                    document.getElementById('lastUpdate').textContent = `最后更新: ${new Date().toLocaleTimeString()}`;
                }
            };

            wsStatus.onclose = () => {
                document.getElementById('connectionStatus').classList.add('error');
                setTimeout(connectStatusWS, 1000);
            };

            wsStatus.onerror = () => {
                document.getElementById('connectionStatus').classList.add('error');
            };
        }

        // WebSocket: 日志
        function connectLogWS(component) {
            if (wsLogs[currentTab]) {
                wsLogs[currentTab].close();
            }

            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${location.host}/ws/logs/${component}`);

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'log') {
                    appendLogLine(data.data);
                }
            };

            wsLogs[component] = ws;
        }

        // 更新状态显示
        function updateStatus(status) {
            for (const [component, info] of Object.entries(status)) {
                const dot = document.getElementById(`${component}_dot`);
                const statusEl = document.getElementById(`${component}_status`);
                const pidEl = document.getElementById(`${component}_pid`);

                if (dot) {
                    dot.className = `status-dot${info.running ? ' running' : ''}`;
                }
                if (statusEl) {
                    statusEl.textContent = info.running ? '运行中' : '已停止';
                }
                if (pidEl) {
                    pidEl.textContent = info.pid ? `PID: ${info.pid}` : '';
                }
            }
        }

        // 添加日志行
        function appendLogLine(line) {
            const viewer = document.getElementById('logViewer');
            const lineEl = document.createElement('div');
            lineEl.className = 'log-line';
            lineEl.innerHTML = formatLogLine(line);
            viewer.appendChild(lineEl);
            viewer.scrollTop = viewer.scrollHeight;

            // 限制最大行数
            while (viewer.children.length > 500) {
                viewer.removeChild(viewer.firstChild);
            }
        }

        // 格式化日志行
        function formatLogLine(line) {
            // 提取时间戳
            const timestampMatch = line.match(/^\\[(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2})\\]/);
            if (timestampMatch) {
                return `<span class="log-timestamp">${timestampMatch[1]}</span>${line.substring(timestampMatch[0].length)}`;
            }
            return line;
        }

        // 切换标签
        function switchTab(component) {
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.toggle('active', tab.textContent.toLowerCase().includes(component.replace('_', '')));
            });

            currentTab = component;
            document.getElementById('logViewer').innerHTML = '';
            connectLogWS(component);
        }

        // 获取状态 (HTTP轮询备用)
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                updateStatus(data.status);
            } catch (e) {
                console.error('Failed to fetch status:', e);
            }
        }

        // 获取统计
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                if (!data.error) {
                    document.getElementById('statTotal').textContent = data.total_messages || 0;
                    document.getElementById('statPending').textContent = data.messages?.pending || 0;
                    document.getElementById('statProcessing').textContent = data.messages?.processing || 0;
                    document.getElementById('statCompleted').textContent = data.messages?.completed || 0;
                    document.getElementById('statFailed').textContent = data.messages?.failed || 0;
                }
            } catch (e) {
                console.error('Failed to fetch stats:', e);
            }
        }

        // 控制操作
        async function controlAction(action) {
            if (!confirm(`确定要 ${action === 'start' ? '启动' : action === 'restart' ? '重启' : '停止'} 所有服务吗？`)) {
                return;
            }

            const btn = document.querySelector(`.btn-${action}`);
            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '执行中...';

            try {
                const res = await fetch(`/api/control/${action}`, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    console.log(`${action} 执行成功`);
                }
            } catch (e) {
                console.error(`Failed to ${action}:`, e);
            } finally {
                setTimeout(() => {
                    btn.disabled = false;
                    btn.textContent = originalText;
                    fetchStatus();
                }, 2000);
            }
        }

        // 定期刷新统计
        setInterval(fetchStats, 1000);
    </script>
</body>
</html>
"""


# ============================================================================
# 启动服务器
# ============================================================================

def run_server(host: str = "0.0.0.0", port: int = 8088):
    """启动 Web 服务器"""
    log(f"🚀 Web 服务器启动中: http://{host}:{port}")
    log(f"📁 项目目录: {PROJECT_ROOT}")
    log(f"📁 日志目录: {LOG_DIR}")

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            use_colors=False
        )
    except Exception as e:
        log(f"❌ Web 服务器启动失败: {e}")
        import traceback
        log(f"❌ 错误详情: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    run_server()
