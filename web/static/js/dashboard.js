const components = ['manager', 'mcp_server', 'discord_bot', 'weixin_bot', 'claude_bridge'];
let wsStatus = null;
let wsLogs = {};
const maxLogLines = 200;

// 主题切换
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('themeIcon');
    if (theme === 'dark') {
        // 月亮图标
        icon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';
    } else {
        // 太阳图标
        icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
}

function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    fetchStatus();
    connectStatusWS();
    components.forEach(comp => connectLogWS(comp));
});

// WebSocket: 状态
function connectStatusWS() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    wsStatus = new WebSocket(`${protocol}//${location.host}/ws/status`);

    wsStatus.onopen = () => {
        updateConnectionStatus(true);
    };

    wsStatus.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'status_update') {
            updateStatus(data.data);
        }
    };

    wsStatus.onclose = () => {
        updateConnectionStatus(false);
        setTimeout(connectStatusWS, 2000);
    };

    wsStatus.onerror = () => {
        updateConnectionStatus(false);
    };
}

// WebSocket: 日志
function connectLogWS(component) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${location.host}/ws/logs/${component}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'log') {
            appendLog(component, data.data);
        }
    };

    ws.onopen = () => {
        updateLogStatusDot(component, true);
    };

    ws.onclose = () => {
        updateLogStatusDot(component, false);
        setTimeout(() => connectLogWS(component), 2000);
    };

    wsLogs[component] = ws;
}

// 更新连接状态
function updateConnectionStatus(connected) {
    const dot = document.getElementById('connectionDot');
    const text = document.getElementById('connectionText');
    dot.className = `connection-dot ${connected ? '' : 'disconnected'}`;
    text.textContent = connected ? '已连接' : '重连中';
}

// 更新日志状态点
function updateLogStatusDot(component, connected) {
    const dot = document.getElementById(`log_${component}_dot`);
    if (dot) {
        dot.className = `log-status-dot ${connected ? 'running' : ''}`;
    }
}

// 更新组件状态
function updateStatus(status) {
    // 更新时间显示
    document.getElementById('lastUpdate').textContent = `最后更新: ${new Date().toLocaleTimeString()}`;

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

        // 同步日志面板的状态点
        if (components.includes(component)) {
            updateLogStatusDot(component, info.running);
        }
    }
}

// 添加日志行
function appendLog(component, line) {
    const viewer = document.getElementById(`log_${component}`);
    if (!viewer) return;

    const lineEl = document.createElement('div');
    lineEl.className = 'log-line';
    lineEl.innerHTML = formatLogLine(line);
    viewer.appendChild(lineEl);

    // 自动滚动到底部
    viewer.scrollTop = viewer.scrollHeight;

    // 限制最大行数
    while (viewer.children.length > maxLogLines) {
        viewer.removeChild(viewer.firstChild);
    }
}

// 格式化日志行
function formatLogLine(line) {
    const timestampMatch = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]/);
    if (timestampMatch) {
        return `<span class="log-timestamp">[${timestampMatch[1]}]</span>${line.substring(timestampMatch[0].length)}`;
    }
    return line;
}

// 清空日志
function clearLog(component) {
    const viewer = document.getElementById(`log_${component}`);
    if (viewer) {
        viewer.innerHTML = '';
    }
}

// 滚动到底部
function scrollToBottom(component) {
    const viewer = document.getElementById(`log_${component}`);
    if (viewer) {
        viewer.scrollTop = viewer.scrollHeight;
    }
}

// 获取状态
async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        updateStatus(data.status);
    } catch (e) {
        console.error('Failed to fetch status:', e);
    }
}

// 控制操作
async function controlAction(action) {
    const actionText = action === 'restart' ? '重启' : '停止';
    if (!confirm(`确定要${actionText}所有服务吗？`)) {
        return;
    }

    const btn = document.querySelector(`.btn-${action}`);
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span>执行中...</span>`;

    try {
        const res = await fetch(`/api/control/${action}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            console.log(`${action} executed successfully`);
        }
    } catch (e) {
        console.error(`Failed to ${action}:`, e);
    } finally {
        setTimeout(() => {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            fetchStatus();
        }, 2000);
    }
}
