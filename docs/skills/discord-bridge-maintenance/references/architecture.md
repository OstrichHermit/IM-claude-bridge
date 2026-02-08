# Discord Claude Bridge - 系统架构

## 项目概述

Discord Claude Bridge 是一个双向桥接系统，将 Discord Bot 与本地 Claude Code CLI 连接，实现 Discord 消息与 Claude AI 的交互。

## 系统架构

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│  Discord    │ ◄──► │ Discord Bot  │ ◄──► │  Message    │
│   用户      │      │  (Bot 层)    │      │   Queue     │
└─────────────┘      └──────────────┘      │  (SQLite)   │
                            │               └──────┬──────┘
                            │                      │
                            ▼                      ▼
                     ┌──────────────┐      ┌──────────────┐
                     │    Task      │      │   Claude     │
                     │   Checker    │      │   Bridge     │
                     └──────────────┘      │  (桥接层)     │
                                           └──────┬───────┘
                                                  │
                                                  ▼
                                           ┌──────────────┐
                                           │  Claude CLI  │
                                           │  (本地执行)   │
                                           └──────────────┘
```

## 组件说明

### 1. Discord Bot (`bot/discord_bot.py`)

**职责**：
- 监听 Discord 消息（支持 @提及和私聊）
- 权限验证（用户和频道白名单）
- 将用户消息写入消息队列
- 轮询队列获取 Claude 响应
- 将响应发送回 Discord（支持 Embed 格式和长消息分割）

**关键方法**：
- `on_message()`: 接收 Discord 消息
- `check_responses()`: 检查并发送 Claude 响应
- `is_authorized()`: 权限检查

**消息格式**：
- 短消息（<4000字符）：Embed 描述
- 长消息：分割为 Embed 字段（最多 25 个，每个 1000 字符）
- 超长消息：发送多个 Embed

### 2. Claude Bridge (`bridge/claude_bridge.py`)

**职责**：
- 从消息队列获取待处理消息
- 调用 Claude CLI (`claude -p "prompt"`)
- 处理超时和重试机制
- 将 Claude 响应写回队列

**关键方法**：
- `process_queue()`: 主循环，处理消息队列
- `call_claude()`: 调用 Claude CLI

**工作目录**：
- 根据 `session_mode` 配置：
  - `none`: 每次新会话
  - `channel`: 每个频道独立会话
  - `user`: 每个用户独立会话
  - `global`: 全局共享会话

### 3. Message Queue (`shared/message_queue.py`)

**职责**：
- SQLite 数据库存储消息
- 消息状态管理（pending → processing → completed/failed）
- 会话管理（sessions 表）

**数据库表**：

#### messages 表
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    direction TEXT,              -- 'to_claude' 或 'to_discord'
    content TEXT,                -- 消息内容
    status TEXT,                 -- 'pending', 'processing', 'completed', 'failed'
    discord_channel_id INTEGER,  -- Discord 频道 ID
    discord_message_id INTEGER,  -- Discord 消息 ID
    discord_user_id INTEGER,     -- Discord 用户 ID
    username TEXT,               -- 用户名
    response TEXT,               -- Claude 响应
    error TEXT,                  -- 错误信息
    is_dm BOOLEAN,               -- 是否私聊
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

#### sessions 表
```sql
CREATE TABLE sessions (
    session_key TEXT PRIMARY KEY,  -- 会话标识
    session_id TEXT,               -- 会话 ID
    created_at TIMESTAMP,
    last_used_at TIMESTAMP
)
```

**消息流转**：

1. Discord Bot 收到消息 → 创建 `direction=to_claude, status=pending` 的消息
2. Claude Bridge 获取消息 → 更新为 `status=processing`
3. Claude CLI 响应 → 更新为 `status=completed, response=...`
4. Discord Bot 读取响应 → 发送到 Discord

### 4. Config (`shared/config.py`)

**职责**：
- 从 `config/config.yaml` 加载配置
- 提供配置属性访问接口

**关键配置**：
- `discord_token`: Discord Bot Token
- `allowed_channels`: 允许的频道 ID 列表
- `allowed_users`: 允许的用户 ID 列表
- `claude_executable`: Claude CLI 可执行文件路径
- `claude_timeout`: Claude 超时时间（秒）
- `session_mode`: 会话模式
- `working_directory`: 工作目录
- `database_path`: 数据库路径

## 启动流程

### 1. 启动 Discord Bot
```bash
cd bot
python discord_bot.py
```

### 2. 启动 Claude Bridge
```bash
cd bridge
python claude_bridge.py
```

**注意**：两个服务需要同时运行才能正常工作。

## 权限控制

### 用户权限
- 如果 `allowed_users` 不为空，只有列表中的用户可以使用 Bot
- 如果 `allowed_users` 为空，所有人都可以使用

### 频道权限
- 如果 `allowed_channels` 不为空，只有在这些频道的消息会被处理
- 如果 `allowed_channels` 为空，所有频道都可以使用

### 私聊支持
- Bot 支持私聊消息（DM）
- 私聊消息不受 `allowed_channels` 限制
- 使用 `isinstance(message.channel, discord.DMChannel)` 检测

## 常见问题

### Bot 无响应
1. 检查 Discord Bot 是否运行
2. 检查 Claude Bridge 是否运行
3. 查看数据库中是否有 `status=pending` 的消息
4. 检查 Claude CLI 是否正常工作

### 权限错误
1. 检查 `config.yaml` 中的 `allowed_users` 和 `allowed_channels`
2. 确认 Discord 用户 ID 和频道 ID 正确

### 私聊用户找不到
- 使用 `await self.fetch_user(user_id)` 而不是 `self.get_user(user_id)`
- 已在 `bot/discord_bot.py:227-236` 修复

## 文件结构

```
discord-claude-bridge/
├── bot/
│   ├── __init__.py
│   └── discord_bot.py       # Discord Bot 主程序
├── bridge/
│   ├── __init__.py
│   └── claude_bridge.py    # Claude Code 桥接服务
├── shared/
│   ├── __init__.py
│   ├── config.py           # 配置管理
│   └── message_queue.py    # 消息队列系统
├── config/
│   ├── config.yaml         # 配置文件
│   └── config.example.yaml # 配置模板
├── sessions/               # 会话工作目录（可选）
├── test_setup.py          # 配置测试脚本
└── README.md              # 项目文档
```

## 技术栈

- **Python**: 3.8+
- **Discord**: discord.py
- **数据库**: SQLite3
- **异步**: asyncio
- **配置**: PyYAML
- **Claude**: Claude Code CLI
