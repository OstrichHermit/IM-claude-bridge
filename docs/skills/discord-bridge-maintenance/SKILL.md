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

### 2. Claude Bridge (`bridge/claude_bridge.py`)
- 从队列获取待处理消息
- 调用 Claude CLI (`claude -p "prompt"`)
- 处理超时和重试机制
- 支持多种会话模式（none/channel/user/global）

### 3. Message Queue (`shared/message_queue.py`)
- SQLite 数据库存储消息
- 消息状态管理（pending → processing → completed/failed）
- 会话管理（sessions 表）

### 4. Config (`shared/config.py`)
- 从 `config/config.yaml` 加载配置
- 提供 Discord Token、权限、Claude 参数等配置

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
使用脚本：
```bash
python scripts/clean_queue.py status
```

输出显示：
- 各状态消息数量（pending/processing/completed/failed）
- 最近的消息列表

### 3. 查看错误日志
使用脚本：
```bash
python scripts/view_logs.py errors
```

### 4. 验证配置
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
# 1. 重启服务
python scripts/start_services.py

# 2. 重置卡住的消息
python scripts/clean_queue.py reset

# 3. 查看日志排查
python scripts/view_logs.py errors
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
- `session_mode`：会话模式（none/channel/user/global）
- `working_directory`：工作目录

#### 队列配置
- `database_path`：数据库路径
- `poll_interval`：轮询间隔（毫秒，默认 500）
- `message_retention_hours`：消息保留时间（小时，默认 24）

**详细配置说明**：查看 `references/config-guide.md`

## 维护脚本

### 启动服务
```bash
python scripts/start_services.py
```
同时启动 Discord Bot 和 Claude Bridge

### 清理队列
```bash
# 查看队列状态
python scripts/clean_queue.py status

# 清理旧消息（24 小时前）
python scripts/clean_queue.py clean 24

# 重置卡住的消息
python scripts/clean_queue.py reset

# 清空所有消息
python scripts/clean_queue.py clear
```

### 查看日志
```bash
# 查看错误日志
python scripts/view_logs.py errors

# 查看所有日志
python scripts/view_logs.py all
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

## 会话模式

根据 `session_mode` 配置，Claude CLI 的工作目录不同：

| 模式 | 工作目录 | 适用场景 |
|------|----------|----------|
| `none` | 临时目录 | 测试、无状态查询 |
| `channel` | `sessions/channel_{id}/` | 多频道独立对话 |
| `user` | `sessions/user_{id}/` | 个性化对话 |
| `global` | 项目根目录 | 单一对话上下文 |

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
1. 运行 python scripts/clean_queue.py status 查看队列
2. 运行 python scripts/view_logs.py errors 查看错误
3. 检查服务是否运行：Get-Process python
4. 如果服务未运行：python scripts/start_services.py
5. 如果有 processing 状态的消息：python scripts/clean_queue.py reset
```

### 示例 2：添加新用户
```
用户：我想让朋友也能用这个 Bot

使用此 Skill：
1. 让用户在 Discord 开启开发者模式
2. 右键点击朋友 → 复制用户 ID
3. 编辑 config/config.yaml
4. 添加用户 ID 到 allowed_users 列表
5. 重启 Discord Bot
```

### 示例 3：修改会话模式
```
用户：我希望每个用户有独立的对话历史

使用此 Skill：
1. 编辑 config/config.yaml
2. 设置 session_mode: "user"
3. 重启 Claude Bridge
4. 验证：sessions/user_{id}/ 目录被创建
```

## 重要提示

- **两个服务必须同时运行**：Discord Bot 和 Claude Bridge 都需要运行才能正常工作
- **私聊使用 fetch_user**：不要使用 `get_user()`，它无法获取不在缓存中的用户
- **数据库位置**：默认在 `shared/messages.db`，确保程序有写入权限
- **Claude CLI 路径**：如果不在 PATH 中，必须在配置中指定完整路径
