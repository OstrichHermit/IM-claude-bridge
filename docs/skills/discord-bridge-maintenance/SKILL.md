---
name: discord-bridge-maintenance
description: 此 Skill 用于维护和管理 Discord Claude Bridge 项目，提供系统架构参考、配置管理、故障排查和维护脚本，适用于调试 Bot 问题、修改配置、清理队列等日常维护任务。
---

# Discord Claude Bridge 维护 Skill

## Skill 用途

此 Skill 专门用于维护 Discord Claude Bridge 项目（位于 `D:\AgentWorkspace\discord-claude-bridge`），提供完整的系统架构知识、配置管理、故障排查工具和维护脚本。

## 何时使用此 Skill

在以下情况使用此 Skill：

- **调试问题**：Bot 无响应、消息发送失败、权限错误
- **配置管理**：修改 Discord Token、权限设置、Claude 参数
- **维护操作**：启动/停止服务、清理队列、查看日志
- **功能扩展**：添加新命令、修改消息格式、优化会话管理
- **系统理解**：了解项目架构、消息流转、数据库结构

## 系统架构概览

Discord Claude Bridge 是一个双向桥接系统，包含以下组件：

### 1. Discord Bot (`bot/discord_bot.py`)
- 监听 Discord 消息（支持 @提及和私聊）
- 权限验证（用户和频道白名单）
- 将消息写入队列并轮询获取响应
- 支持长消息分割和 Embed 格式
- **消息追踪系统**：实时状态提示（PENDING → PROCESSING → AI_STARTED → COMPLETED）
- **启动通知**：Bot 启动时发送通知（可配置频道或私聊）
- **斜杠命令**：`/new` 重置会话、`/status` 查看状态、`/restart` 重启服务

### 2. Claude Bridge (`bridge/claude_bridge.py`)
- 从队列获取待处理消息
- 调用 Claude CLI (`claude -p "prompt"`)
- 处理超时和重试机制
- **全局会话模式**：所有对话共享同一个上下文
- **Session ID 管理**：使用 `--session-id` 参数精确控制会话
- **频道自动识别**：在消息格式中包含频道 ID，方便 Claude Code 识别
  - 频道消息：`来自频道（{channel_id}）的{username}（{user_id}）说：{message}`
  - 私聊消息：保持原格式 `{username}（{user_id}）说：{message}`

### 3. MCP Server (`mcp_server/`)
- **MCP 服务器**：为 Claude Code 提供文件发送到 Discord 的能力
- **工具层** (`tools/discord_tools.py`)：
  - `mcp_send_file_to_discord`：发送单个文件
  - `mcp_send_multiple_files_to_discord`：批量发送（最多 10 个）
  - `mcp_list_discord_channels`：列出可访问的频道
- **服务层** (`services/discord_service.py`)：
  - 通过消息队列与 Discord Bot 通信
  - 无需创建新的 Discord 客户端
  - 支持用户私聊和频道发送
  - 支持 Embed 精美格式

### 4. Message Queue (`shared/message_queue.py`)
- SQLite 数据库存储消息
- 消息状态管理（pending → processing → ai_started → completed/failed）
- 会话管理（sessions 表）
- 文件请求表（file_requests）：支持 MCP 发送文件
- **文件下载请求表**（file_download_requests）：支持从 Discord 下载附件

### 5. Config (`shared/config.py`)
- 从 `config/config.yaml` 加载配置
- 提供 Discord Token、权限、Claude 参数等配置
- **文件下载配置**：默认下载目录、允许的下载目录列表

**详细架构说明**：查看 `references/architecture.md`

## 快速诊断流程

当用户报告问题时，按以下流程诊断：

### 1. 确认服务运行状态
```bash
# 检查 Python 进程
Get-Process | Where-Object {$_.ProcessName -like "*python*"}

# 或查看控制台是否有输出
```

### 2. 检查数据库状态
直接查询数据库：
```bash
# SQLite 查询
sqlite3 shared/messages.db "SELECT status, COUNT(*) FROM messages GROUP BY status"

# 或在 Python 中
python -c "import sqlite3; conn = sqlite3.connect('shared/messages.db'); print(conn.execute('SELECT status, COUNT(*) FROM messages GROUP BY status').fetchall())"
```

### 3. 验证配置
```bash
python scripts/test_config.py
```

## 常见问题解决

### Bot 无响应

**可能原因**：
1. Discord Bot 或 Claude Bridge 未运行
2. 消息卡在 processing 状态（Claude CLI 超时）
3. 数据库锁定

**解决方案**：
```bash
# 1. 重启服务（Windows）
restart.bat

# 2. 手动重置卡住的消息
sqlite3 shared/messages.db "UPDATE messages SET status = 'pending' WHERE status = 'processing'"

# 3. 查看日志
# 直接查看控制台输出或检查日志文件
```

### 权限错误

**症状**：Bot 返回权限不足

**诊断**：
1. 检查 `config.yaml` 中的 `allowed_users` 和 `allowed_channels`
2. 确认 Discord 用户 ID 和频道 ID 正确（使用开发者模式获取）

**解决**：
- 添加用户/频道 ID 到允许列表
- 或设置为空列表允许所有用户/频道

**详细配置说明**：查看 `references/config-guide.md`

### Claude CLI 错误

**常见错误**：
- `FileNotFoundError: claude not found`：Claude CLI 不在 PATH 中
- `TimeoutError`：调用超时，增加 `timeout` 配置
- `AuthenticationError`：Claude CLI 未登录

**解决**：
```bash
# 1. 检查 Claude CLI
claude --version

# 2. 登录
claude auth login

# 3. 在 config.yaml 中指定完整路径
claude:
  executable: "/absolute/path/to/claude"
```

### 私聊问题

**症状**：私聊 Bot 无响应或"找不到用户"

**已修复**：使用 `await self.fetch_user(user_id)` 而不是 `self.get_user(user_id)`

**验证**：检查 `bot/discord_bot.py:227-236`

### 消息重复或丢失

**原因**：
- 多个 Bot/Bridge 进程同时运行
- 轮询间隔太长

**解决**：
1. 确保只有一个进程在运行
2. 调整轮询间隔：
   ```yaml
   queue:
     poll_interval: 300  # 减少到 300 毫秒
   ```

### 文件下载问题

#### 下载超时

**症状**：提示"下载超时（120秒）"，但文件实际已下载成功

**已修复**：使用轮询检查状态（每 2 秒检查一次），不再依赖队列超时

**验证**：检查 `bot/discord_bot.py:505-638` 的 `monitor_download_progress` 函数

#### 下载目录不存在

**症状**：提示"无效的保存目录"

**解决方案**：
1. 检查 `config.yaml` 中的 `file_download.default_directory` 配置
2. 确保目录路径正确（支持相对路径和绝对路径）
3. Bot 会自动创建目录

#### 文件名冲突

**症状**：下载多个同名文件时覆盖

**已解决**：自动重命名（`file.jpg` → `file_1.jpg` → `file_2.jpg`）

**代码位置**：`bot/discord_bot.py:955-962`

#### 查看下载记录

**查询数据库**：
```bash
# 查看最近的下载请求
sqlite3 shared/messages.db "SELECT id, save_directory, status, downloaded_files FROM file_download_requests ORDER BY id DESC LIMIT 5"

# 解析 JSON 字段
python -c "
import sqlite3, json
conn = sqlite3.connect('shared/messages.db')
cursor = conn.cursor()
cursor.execute('SELECT downloaded_files FROM file_download_requests WHERE id = 5')
result = cursor.fetchone()
if result and result[0]:
    files = json.loads(result[0])
    print(files)
conn.close()
"
```

### MCP 工具无法发送文件

**症状**：Claude Code 调用 MCP 工具时失败或无响应

**可能原因**：
1. MCP 服务器未启动或配置错误
2. Discord Bot 未运行
3. 权限配置问题（工具未被允许）

**诊断**：
```bash
# 1. 检查 Claude Code 配置
# 查看 Claude Code 配置文件中是否正确配置了 MCP 服务器
# Windows: %APPDATA%\Claude\claude_desktop_config.json
# macOS/Linux: ~/Library/Application Support/Claude/claude_desktop_config.json

# 2. 检查 Discord Bot 是否运行
Get-Process | Where-Object {$_.ProcessName -like "*python*" -and $_.MainWindowTitle -like "*discord*"}

# 3. 测试 MCP 服务器
python test_setup.py
```

**解决**：
1. 确保 MCP 服务器配置正确（参考 `MCP_SETUP.md`）
2. 确认工具权限已添加到 `settings.local.json`：
   ```json
   {
     "permissions": {
       "allow": [
         "mcp__discord-bridge__mcp_send_file_to_discord",
         "mcp__discord-bridge__mcp_send_multiple_files_to_discord",
         "mcp__discord-bridge__mcp_list_discord_channels"
       ]
     }
   }
   ```
3. 重启 Claude Code 应用
4. 确保 Discord Bot 正在运行

**详细配置**：查看 `MCP_SETUP.md`

## 配置管理

### 配置文件位置
```
discord-claude-bridge/config/config.yaml
```

### 关键配置项

#### Discord 配置
- `token`：Discord Bot Token（必需）
- `command_prefix`：命令前缀（默认 `@`）
- `allowed_channels`：允许的频道 ID 列表
- `allowed_users`：允许的用户 ID 列表

#### Claude 配置
- `executable`：Claude CLI 路径
- `timeout`：超时时间（秒，默认 300）
- `max_retries`：最大重试次数（默认 3）
- `working_directory`：工作目录（留空则使用项目根目录）

#### 队列配置
- `database_path`：数据库路径
- `poll_interval`：轮询间隔（毫秒，默认 500）
- `message_retention_hours`：消息保留时间（小时，默认 24）

**详细配置说明**：查看 `references/config-guide.md`

## 维护脚本

### 启动服务
```bash
# Windows
start.bat

# Linux/Mac
chmod +x start.sh
./start.sh
```

### 重启服务
```bash
# Windows（推荐）
restart.bat
```

`restart.bat` 会自动：
1. 关闭所有 Discord Bridge 窗口
2. 终止旧的 Python 进程
3. 重新启动 Discord Bot 和 Claude Bridge

### 清理数据库
```bash
# 直接操作 SQLite
sqlite3 shared/messages.db

# 清理旧消息（示例：删除 24 小时前的已完成消息）
DELETE FROM messages WHERE status = 'completed' AND created_at < datetime('now', '-24 hours');

# 重置卡住的消息
UPDATE messages SET status = 'pending' WHERE status = 'processing';
```

### 验证配置
```bash
python scripts/test_config.py
```
检查配置文件格式、数据库连接、Claude CLI 可用性

## 数据库结构

### messages 表
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    direction TEXT,              -- 'to_claude' 或 'to_discord'
    content TEXT,                -- 消息内容
    status TEXT,                 -- 'pending', 'processing', 'completed', 'failed'
    discord_channel_id INTEGER,
    discord_message_id INTEGER,
    discord_user_id INTEGER,
    username TEXT,
    response TEXT,               -- Claude 响应
    error TEXT,                  -- 错误信息
    is_dm BOOLEAN,               -- 是否私聊
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

### sessions 表
```sql
CREATE TABLE sessions (
    session_key TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TIMESTAMP,
    last_used_at TIMESTAMP
)
```

## 会话管理

项目使用**全局会话模式**：
- 所有 Discord 消息共享同一个 Claude Code 会话
- 对话上下文持续保持，直到使用 `/new` 命令重置
- Session ID 存储在数据库的 `sessions` 表中
- `/new` 命令会删除当前会话并生成新的 Session ID

**会话重置示例**：
```
用户: /new
Bot: ✅ 会话已重置！开始新的对话上下文。
```

## 权限控制

### 用户权限
- `allowed_users` 为空：所有用户可用
- `allowed_users` 不为空：只有列表中的用户可用

### 频道权限
- `allowed_channels` 为空：所有频道可用
- `allowed_channels` 不为空：只有这些频道的消息被处理

### 私聊支持
- Bot 支持私聊消息（DM）
- 私聊不受 `allowed_channels` 限制
- 使用 `isinstance(message.channel, discord.DMChannel)` 检测

## 安全建议

1. **保护 Token**：
   - 不要将 `config.yaml` 提交到版本控制
   - 使用 `config.example.yaml` 作为模板
   - 在 `.gitignore` 中添加 `config/config.yaml`

2. **限制访问**：
   - 生产环境使用 `allowed_users` 限制用户
   - 使用 `allowed_channels` 限制频道

3. **隔离环境**：
   - 测试和生产使用不同的 Bot Token
   - 使用不同的数据库文件

## 参考文档

- **系统架构**：`references/architecture.md` - 详细的系统架构说明
- **配置指南**：`references/config-guide.md` - 完整的配置项说明
- **故障排查**：`references/troubleshooting.md` - 常见问题和解决方案

## 使用示例

### 示例 1：Bot 不响应消息
```
用户：Discord Bot 在线但不回复消息

使用此 Skill：
1. 检查服务是否运行：Get-Process python
2. 检查数据库状态：sqlite3 shared/messages.db "SELECT status, COUNT(*) FROM messages GROUP BY status"
3. 如果服务未运行：restart.bat
4. 如果有 processing 状态的消息：手动重置或重启服务
5. 查看控制台输出排查错误
```

### 示例 2：添加新用户
```
用户：我想让朋友也能用这个 Bot

使用此 Skill：
1. 让用户在 Discord 开启开发者模式
2. 右键点击朋友 → 复制用户 ID
3. 编辑 config/config.yaml
4. 添加用户 ID 到 allowed_users 列表
5. 重启服务：restart.bat
```

### 示例 3：重置会话
```
用户：我想重新开始一个新对话

在 Discord 中：
1. 发送斜杠命令：/new
2. Bot 会删除当前会话并创建新的 session_id
3. 之后的对话将使用新的上下文
```

### 示例 4：配置 MCP 服务器
```
用户：我想让 Claude Code 能够发送文件到 Discord

使用此 Skill：
1. 阅读 MCP_SETUP.md 了解详细配置步骤
2. 编辑 Claude Code 配置文件：
   - Windows: %APPDATA%\Claude\claude_desktop_config.json
   - macOS/Linux: ~/Library/Application Support/Claude/claude_desktop_config.json
3. 添加 MCP 服务器配置：
   {
     "mcpServers": {
       "discord-bridge": {
         "command": "python",
         "args": ["D:\\AgentWorkspace\\discord-claude-bridge\\mcp_server\\server.py"],
         "env": {"PYTHONPATH": "D:\\AgentWorkspace\\discord-claude-bridge"}
       }
     }
   }
4. 在 .claude/settings.local.json 中添加工具权限：
   {
     "permissions": {
       "allow": [
         "mcp__discord-bridge__mcp_send_file_to_discord",
         "mcp__discord-bridge__mcp_send_multiple_files_to_discord",
         "mcp__discord-bridge__mcp_list_discord_channels"
       ]
     }
   }
5. 重启 Claude Code 应用
```

### 示例 5：使用 MCP 发送文件
```
用户（在 Discord 频道中）：请把根目录下的新闻汇总 PDF 发过来

Claude Code 会自动：
1. 从消息格式中解析频道 ID：`来自频道（1466858871720251425）的鸵鸟居士说：...`
2. 调用 MCP 工具 `mcp_send_file_to_discord`
3. 发送文件到该频道，无需手动指定频道 ID
```

## 重要提示

- **两个服务必须同时运行**：Discord Bot 和 Claude Bridge 都需要运行才能正常工作
- **私聊使用 fetch_user**：不要使用 `get_user()`，它无法获取不在缓存中的用户
- **数据库位置**：默认在 `shared/messages.db`，确保程序有写入权限
- **Claude CLI 路径**：如果不在 PATH 中，必须在配置中指定完整路径
- **消息追踪**：Bot 会自动追踪每条消息的状态并实时更新（PENDING → PROCESSING → AI_STARTED → COMPLETED）
- **斜杠命令**：使用 `/new` 重置会话，`/status` 查看状态，`/restart` 重启服务
- **启动通知**：Bot 启动时会发送通知到配置的频道或用户
- **MCP 服务器**：为 Claude Code 提供文件发送能力，需要单独配置（参考 `MCP_SETUP.md`）
- **频道自动识别**：消息格式包含频道 ID，Claude Code 可自动识别并发送回原频道
- **工具权限**：使用 MCP 工具前，必须在 `settings.local.json` 中添加相应权限
