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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from shared.logger import get_logger, LOG_DIR, cleanup_logs
from shared.config import Config

log = get_logger("WebServer", "manager")


# ============================================================================
# 日志配置
# ============================================================================

# 组件名 → 日志文件路径的映射（用于 Web API 端点）
LOG_FILES = {
    "discord_bot": LOG_DIR / "discord_bot.log",
    "weixin_bot": LOG_DIR / "weixin_bot.log",
    "claude_bridge": LOG_DIR / "claude_bridge.log",
    "manager": LOG_DIR / "manager.log",
    "mcp_server": LOG_DIR / "mcp_server.log",
}


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

# 禁用静态文件缓存
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
app.add_middleware(NoCacheMiddleware)

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
        log.log(f"⚠️  查找进程失败 ({pattern}): {e}")
    return None


def get_component_status(component: str) -> Dict:
    """获取组件状态"""
    patterns = {
        "discord_bot": "discord_bot.py",
        "weixin_bot": "weixin_bot.py",
        "claude_bridge": "claude_bridge.py",
        "manager": "im_claude_bridge_manager.py",
        "web_server": "web_server.py",
        "mcp_server": "mcp_server",
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
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
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
    components = ["discord_bot", "weixin_bot", "claude_bridge", "manager", "web_server", "mcp_server"]
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
        log.log("🛑 收到停止请求")
    elif action == "restart":
        script_path = script_dir / "restart.bat"
        log.log("🔄 收到重启请求")
    elif action == "start":
        script_path = script_dir / "start.bat"
        log.log("🚀 收到启动请求")
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
        log.log(f"❌ 控制命令执行失败: {e}")
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
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                # 跳到文件末尾
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        await websocket.send_json({"type": "log", "data": line.strip()})
                    else:
                        await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        log.log(f"WebSocket 客户端断开: {component}")
    except Exception as e:
        log.log(f"WebSocket 错误 ({component}): {e}")
    finally:
        manager_ws.disconnect(websocket, f"logs_{component}")


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """WebSocket 状态推送"""
    await manager_ws.connect(websocket, "status")
    try:
        await websocket.send_json({"type": "connected", "channel": "status"})

        while True:
            components = ["discord_bot", "weixin_bot", "claude_bridge", "manager", "web_server", "mcp_server"]
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
        log.log(f"Status WebSocket 错误: {e}")
    finally:
        manager_ws.disconnect(websocket, "status")


# ============================================================================
# HTML 页面
# ============================================================================

@app.get("/")
async def get_dashboard():
    """主页仪表盘"""
    html_path = PROJECT_ROOT / "web" / "templates" / "dashboard.html"
    return FileResponse(str(html_path))


# 静态文件服务
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "web" / "static")), name="static")


# ============================================================================
# 启动服务器
# ============================================================================

def run_server(host: str = None, port: int = None):
    """启动 Web 服务器"""
    # 从配置文件读取默认值
    config = Config()
    if host is None:
        host = config.web_server_host
    if port is None:
        port = config.web_server_port

    # 启动时清理日志（保留最近1000行）
    cleanup_logs()
    log.log(f"🚀 Web 服务器启动中: http://{host}:{port}")
    log.log(f"📁 项目目录: {PROJECT_ROOT}")
    log.log(f"📁 日志目录: {LOG_DIR}")

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
        log.log(f"❌ Web 服务器启动失败: {e}")
        import traceback
        log.log(f"❌ 错误详情: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    import argparse

    # 从配置文件获取默认值
    try:
        config = Config()
        default_host = config.web_server_host
        default_port = config.web_server_port
    except Exception:
        default_host = "0.0.0.0"
        default_port = 8088

    parser = argparse.ArgumentParser(description='IM-Claude-Bridge Web Server')
    parser.add_argument('--host', default=default_host, help=f'监听地址（默认: {default_host}）')
    parser.add_argument('--port', type=int, default=default_port, help=f'监听端口（默认: {default_port}）')

    args = parser.parse_args()

    run_server(host=args.host, port=args.port)
