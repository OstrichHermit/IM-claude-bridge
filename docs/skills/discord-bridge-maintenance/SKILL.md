---
name: discord-bridge-maintenance
description: Discord Claude Bridge 项目维护指南，包含系统架构、技术实现、配置管理和故障排查。
---

# Discord Claude Bridge 技术实现

## 项目结构

```
discord-claude-bridge/
├── bot/                    # Discord Bot 层
│   └── discord_bot.py      # Discord 消息监听、响应、文件下载
├── bridge/                 # Claude Bridge 层
│   └── claude_bridge.py    # 调用 Claude CLI 处理消息
├── mcp_server/             # MCP 服务器
│   ├── server.py           # MCP 服务入口
│   ├── tools/              # MCP 工具实现
│   │   └── discord_tools.py
│   └── services/           # 服务层
│       └── discord_service.py
├── shared/                 # 共享模块
│   ├── message_queue.py    # 消息队列（SQLite）
│   └── config.py           # 配置管理
└── config/
    └── config.yaml         # 配置文件
```

## 核心组件

### 1. Discord Bot (`bot/discord_bot.py`)

**职责**：
- 监听 Discord 消息（@提及和私聊）
- 权限验证（用户和频道白名单）
- 将消息写入队列
- 轮询队列获取响应并发送到 Discord
- 处理文件下载请求

**关键函数**：
- `on_message()` - 接收 Discord 消息
- `process_queue_messages()` - 轮询队列发送响应
- `monitor_download_progress()` - 监控文件下载（轮询方式，每 2 秒检查一次）
- 斜杠命令：`/new`、`/status`、`/restart`

**消息追踪状态**：
```
PENDING → PROCESSING → AI_STARTED → COMPLETED
```

### 2. Claude Bridge (`bridge/claude_bridge.py`)

**职责**：
- 从队列获取待处理消息
- 调用 Claude CLI：`claude -p "prompt" --session-id <id>`
- 处理超时和重试机制
- 将响应写入队列

**全局会话模式**：
- 所有对话共享同一个 Claude Code 会话
- Session ID 存储在数据库 `sessions` 表
- `/new` 命令重置会话（删除并生成新 Session ID）

**频道自动识别**：
- 频道消息格式：`来自频道（{channel_id}）的{username}（{user_id}）说：{message}`
- 私聊消息格式：`{username}（{user_id}）说：{message}`

### 3. MCP Server (`mcp_server/`)

**架构**：
- **服务层** (`services/discord_service.py`)：通过消息队列与 Discord Bot 通信
- **工具层** (`tools/discord_tools.py`)：
  - `mcp_send_file_to_discord` - 发送单个文件
  - `mcp_send_multiple_files_to_discord` - 批量发送（最多 10 个）
  - `mcp_list_discord_channels` - 列出可访问频道

**特点**：
- 复用 Discord Bot 客户端，无需创建新连接
- 支持用户私聊和频道发送
- 支持 Embed 精美格式

### 4. Message Queue (`shared/message_queue.py`)

**数据库表**：

```sql
-- 消息表
messages (
    id, direction, content, status,
    discord_channel_id, discord_message_id, discord_user_id,
    username, response, error, is_dm,
    created_at, updated_at
)

-- 会话表
sessions (
    session_key, session_id, created_at, last_used_at
)

-- 文件请求表（MCP 发送）
file_requests (
    id, channel_id, user_id, file_path, status
)

-- 文件下载请求表（Discord 下载）
file_download_requests (
    id, channel_id, user_id, save_directory,
    downloaded_files, status, error
)
```

**消息状态流转**：
```
pending → processing → ai_started → completed/failed
```

### 5. Config (`shared/config.py`)

**配置项**：

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"
  command_prefix: "@"
  allowed_channels: []      # 空 = 所有频道
  allowed_users: []         # 空 = 所有用户

claude:
  executable: "claude"
  timeout: 300              # 秒
  max_retries: 3
  working_directory: ""

file_download:
  default_directory: "D:/AgentWorkspace/downloads"

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500        # 毫秒
  message_retention_hours: 24
```

## 关键实现细节

### 文件下载（轮询监控）

位置：`bot/discord_bot.py:505-638`

```python
async def monitor_download_progress(self, request_id: int, channel, confirmation_msg):
    """监控文件下载进度（轮询方式）"""
    max_wait_time = 120  # 最大等待 120 秒
    check_interval = 2   # 每 2 秒检查一次
    elapsed = 0

    while elapsed < max_wait_time:
        # 直接查询数据库状态
        conn = sqlite3.connect(self.config.database_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT status, downloaded_files, save_directory, error
            FROM file_download_requests
            WHERE id = ?
        """, (request_id,))
        db_result = cursor.fetchone()
        conn.close()

        if db_result and db_result[0] == 'completed':
            # 下载完成，显示成功消息
            ...

        await asyncio.sleep(check_interval)
        elapsed += check_interval
```

### 私聊支持

位置：`bot/discord_bot.py:227-236`

```python
# 使用 fetch_user 而不是 get_user
user = await self.fetch_user(user_id)
await user.send(response)
```

### 消息分割

位置：`bot/discord_bot.py`

- Discord 消息限制：2000 字符
- 长消息自动分割为多条
- Embed 格式支持精美展示

### 权限控制

- **用户权限**：`allowed_users` 为空则允许所有用户
- **频道权限**：`allowed_channels` 为空则允许所有频道
- **私聊不受频道限制**

## 常见问题

### Bot 无响应

1. 检查进程：`Get-Process python`
2. 重启服务：`restart.bat`
3. 重置卡住的消息：`UPDATE messages SET status = 'pending' WHERE status = 'processing'`

### Claude CLI 错误

1. 检查 CLI：`claude --version`
2. 登录：`claude auth login`
3. 配置完整路径：`executable: "/absolute/path/to/claude"`

### 下载超时

- 已修复：使用轮询检查（每 2 秒）
- 最大等待 120 秒
- 超时后会最后检查一次数据库

### MCP 工具无法使用

1. 检查 MCP 服务器配置（`%APPDATA%\Claude\claude_desktop_config.json`）
2. 添加工具权限到 `.claude/settings.local.json`
3. 确保 Discord Bot 正在运行

## 数据库操作

```bash
# 查看消息状态统计
sqlite3 shared/messages.db "SELECT status, COUNT(*) FROM messages GROUP BY status"

# 查看最近的下载请求
sqlite3 shared/messages.db "SELECT id, save_directory, status FROM file_download_requests ORDER BY id DESC LIMIT 5"

# 重置卡住的消息
sqlite3 shared/messages.db "UPDATE messages SET status = 'pending' WHERE status = 'processing'"

# 清理旧消息
DELETE FROM messages WHERE status = 'completed' AND created_at < datetime('now', '-24 hours');
```

## 配置文件位置

- **主配置**：`config/config.yaml`
- **Claude MCP 配置**：`%APPDATA%\Claude\claude_desktop_config.json`
- **工具权限**：`.claude/settings.local.json`

## 启动脚本

- **启动**：`start.bat`
- **重启**：`restart.bat`（自动关闭旧进程并重启）
