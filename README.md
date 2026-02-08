# Discord Claude Bridge

<div align="center">

**[English](#english) | [简体中文](#简体中文)**

</div>

---

## 简体中文

将 Discord 消息桥接到本地 Claude Code 的双向通信系统。

## 功能特性

- ✅ 接收 Discord 中 @Bot 的消息
- ✅ 将消息转发给本地 Claude Code CLI
- ✅ 接收 Claude Code 的回复并发送回 Discord
- ✅ 基于消息队列的异步处理
- ✅ 支持权限控制（频道、用户）
- ✅ 消息持久化和状态跟踪

## 系统架构

```
Discord <---> Discord Bot <---> SQLite 消息队列 <---> Claude 桥接服务 <---> Claude Code CLI
```

## 项目结构

```
discord-claude-bridge/
├── bot/
│   └── discord_bot.py      # Discord Bot 主程序
├── bridge/
│   └── claude_bridge.py    # Claude Code 桥接服务
├── shared/
│   ├── config.py           # 配置管理
│   ├── message_queue.py    # 消息队列系统
│   └── messages.db         # 消息数据库（运行时生成）
├── config/
│   ├── config.example.yaml # 配置文件示例
│   └── config.yaml         # 实际配置文件（需创建）
├── docs/
│   └── skills/
│       └── discord-bridge-maintenance/  # Claude Code Skill（维护工具）
│           ├── SKILL.md                 # 核心 Skill 指导文档
│           ├── references/              # 参考文档（架构、配置、故障排查）
│           └── scripts/                 # 维护脚本（启动、清理、诊断）
├── requirements.txt        # Python 依赖
├── start.bat              # Windows 启动脚本
├── start.sh               # Linux/Mac 启动脚本
└── README.md              # 本文件
```

## 🤖 Claude Code Skill

本项目包含一个专门的维护 Skill（`discord-bridge-maintenance`），用于帮助维护和调试 Discord Bridge。

### 安装 Skill

将 Skill 安装到 Claude Code：

```bash
# 复制 Skill 到 Claude Code skills 目录
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# Windows 用户
xcopy /E /I docs\skills\discord-bridge-maintenance %USERPROFILE%\.claude\skills\discord-bridge-maintenance
```

### Skill 功能

安装后，当您需要维护或调试 Discord Bridge 时，Claude Code 会自动加载此 Skill，提供：

- **快速诊断流程**：服务状态检查、数据库状态查看、日志分析
- **配置管理**：详细的配置项说明和修改指导
- **故障排查**：常见问题解决方案（Bot 无响应、权限错误、Claude CLI 错误等）
- **维护脚本**：一键启动服务、清理队列、验证配置

### 使用方法

在 Claude Code 中，只需描述您遇到的问题，例如：

- "Discord Bot 不响应消息"
- "我想添加新的管理员用户"
- "如何修改会话模式"

Claude Code 会自动加载 Skill 并提供针对性的帮助。

---

## 快速开始

### 1. 前置要求

- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 Discord Bot

#### 创建 Discord 应用

1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)
2. 点击 "New Application" 创建应用
3. 在 "Bot" 页面创建 Bot 并复制 Token
4. 在 "OAuth2" -> "URL Generator" 中勾选：
   - `bot`
   - `messages.read`
   - `messages.write`
5. 生成的 URL 用于邀请 Bot 到服务器

#### 配置权限

在 Developer Portal 的 Bot 页面：
- **Privileged Gateway Intents**:
  - ✅ Message Content Intent
  - ✅ Server Members Intent（可选）

### 4. 配置项目

复制配置文件并编辑：

```bash
cd config
copy config.example.yaml config.yaml
notepad config.yaml  # 或使用其他编辑器
```

编辑 `config.yaml`：

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN_HERE"  # 替换为你的 Token
  command_prefix: "@"
  allowed_channels: []                   # 空列表 = 所有频道
  allowed_users: []                      # 空列表 = 所有用户

claude:
  executable: "claude-code"              # Claude Code CLI 命令
  timeout: 300                           # 超时时间（秒）
  max_retries: 3                         # 最大重试次数

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                     # 轮询间隔（毫秒）
  message_retention_hours: 24            # 消息保留时间
```

### 5. 启动服务

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

或分别启动两个组件：

```bash
# 终端 1: 启动 Discord Bot
python bot/discord_bot.py

# 终端 2: 启动 Claude 桥接服务
python bridge/claude_bridge.py
```

### 6. 使用方法

在 Discord 中：

```
@YourBot 请帮我分析这段代码
```

Bot 会：
1. 接收消息
2. 显示"消息已接收"确认
3. 转发给本地 Claude Code 处理
4. 将 Claude 的真实回复发送回 Discord

### 7. 验证 Claude Code CLI

在启动服务前，确保 Claude Code CLI 可用：

```bash
# 测试命令
claude -p "你好，请简短回复"

# 如果看到 Claude 的响应，说明 CLI 已正确安装
```

## 配置选项

### 权限控制

**限制特定频道**：
```yaml
allowed_channels: [123456789012345678, 987654321098765432]
```

**限制特定用户**：
```yaml
allowed_users: [123456789012345678, 987654321098765432]
```

### Claude Code 集成

项目已实现真实的 Claude Code CLI 调用，并支持**持续对话**功能！

**工作原理**：
- 使用 `claude -p "提示词"` 命令进行非交互式调用
- 自动捕获 Claude 的响应并返回给 Discord
- 支持重试机制和超时控制
- **支持会话持久化，保持对话上下文**
- 通过为每个会话创建独立工作目录实现对话隔离

**可选配置**：

```yaml
claude:
  executable: "claude"              # Claude CLI 命令（通常就是 "claude"）
  timeout: 300                       # 单次请求超时时间（秒）
  max_retries: 3                     # 失败重试次数
  working_directory: ""              # 基础工作目录（可选）
  session_mode: "channel"            # 会话模式（见下文说明）
```

**会话模式说明**：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `"channel"` | 每个 Discord 频道独立会话 | **推荐**：同一频道的对话能保持上下文 |
| `"user"` | 每个用户独立会话 | 用户在不同频道的对话保持一致 |
| `"global"` | 全局共享会话 | 所有人共享同一个对话上下文 |
| `"none"` | 每次都是新对话 | 默认模式，不保持上下文 |

**工作目录说明**：
- 留空（默认）：使用项目根目录
- 设置为特定路径：让 Claude 可以访问特定项目文件
- 例如：`working_directory: "D:/MyProject"`
- 会话目录会自动创建在 `{working_directory}/sessions/{session_key}/`

**持续对话示例**：
```
你: @OH-Bot 我的名字是张三
Bot: ✅ 消息已接收...
Bot: ✨ 来自 Claude 的回复: 你好张三！很高兴认识你。

你: @OH-Bot 我叫什么名字？
Bot: ✅ 消息已接收...
Bot: ✨ 来自 Claude 的回复: 你叫张三。（Claude 记住了之前的对话！）
```

## 故障排查

### Bot 无响应

1. 检查 Discord Token 是否正确
2. 确认 Bot 有足够的权限
3. 确认已启用 Message Content Intent

### Claude Code 未响应

1. 测试 CLI 是否可用：
   ```bash
   claude -p "测试"
   ```
2. 检查 Claude Code 是否已登录：
   ```bash
   claude --version
   ```
3. 查看桥接服务窗口的详细错误日志
4. 如果提示找不到 claude 命令：
   - 确保 Claude Code 已安装
   - 重启终端/命令行窗口
   - 检查 PATH 环境变量

### 权限错误

1. 检查配置文件中的频道/用户 ID
2. 确认 Bot 在服务器中有相应权限

## 开发说明

### 消息状态流转

```
PENDING -> PROCESSING -> COMPLETED
                |
                v
             FAILED
```

### 数据库结构

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    direction TEXT,              -- 'to_claude' 或 'to_discord'
    content TEXT,                -- 消息内容
    status TEXT,                 -- 消息状态
    discord_channel_id INTEGER,  -- Discord 频道 ID
    discord_message_id INTEGER,  -- Discord 消息 ID
    discord_user_id INTEGER,     -- 用户 ID
    username TEXT,               -- 用户名
    response TEXT,               -- Claude 的响应
    error TEXT,                  -- 错误信息
    created_at TIMESTAMP,        -- 创建时间
    updated_at TIMESTAMP         -- 更新时间
);
```

## 安全建议

- 不要提交 `config.yaml` 到版本控制
- 定期清理消息数据库
- 在生产环境使用受限的用户/频道权限
- 使用环境变量存储敏感信息

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

## English

A bidirectional communication system that bridges Discord messages to your local Claude Code CLI.

## Features

- ✅ Receive @Bot messages from Discord
- ✅ Forward messages to local Claude Code CLI
- ✅ Receive Claude Code responses and send back to Discord
- ✅ Async processing based on message queue
- ✅ Support permission control (channels, users)
- ✅ Message persistence and status tracking

## System Architecture

```
Discord <---> Discord Bot <---> SQLite Message Queue <---> Claude Bridge Service <---> Claude Code CLI
```

## Project Structure

```
discord-claude-bridge/
├── bot/
│   └── discord_bot.py      # Discord Bot main program
├── bridge/
│   └── claude_bridge.py    # Claude Code bridge service
├── shared/
│   ├── config.py           # Configuration management
│   ├── message_queue.py    # Message queue system
│   └── messages.db         # Message database (generated at runtime)
├── config/
│   ├── config.example.yaml # Configuration file example
│   └── config.yaml         # Actual configuration file (to be created)
├── docs/
│   └── skills/
│       └── discord-bridge-maintenance/  # Claude Code Skill (maintenance tool)
│           ├── SKILL.md                 # Core Skill guide
│           ├── references/              # Documentation (architecture, config, troubleshooting)
│           └── scripts/                 # Maintenance scripts (start, clean, diagnostics)
├── requirements.txt        # Python dependencies
├── start.bat              # Windows startup script
├── start.sh               # Linux/Mac startup script
└── README.md              # This file
```

## 🤖 Claude Code Skill

This project includes a dedicated maintenance Skill (`discord-bridge-maintenance`) to help you maintain and debug Discord Bridge.

### Install Skill

Install the Skill to Claude Code:

```bash
# Copy Skill to Claude Code skills directory
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# Windows users
xcopy /E /I docs\skills\discord-bridge-maintenance %USERPROFILE%\.claude\skills\discord-bridge-maintenance
```

### Skill Features

Once installed, when you need to maintain or debug Discord Bridge, Claude Code will automatically load this Skill and provide:

- **Quick diagnostic workflow**: Service status check, database status view, log analysis
- **Configuration management**: Detailed configuration item explanations and modification guidance
- **Troubleshooting**: Solutions to common problems (Bot unresponsive, permission errors, Claude CLI errors, etc.)
- **Maintenance scripts**: One-click service start, queue cleanup, configuration verification

### Usage

In Claude Code, simply describe the problem you encounter, for example:

- "Discord Bot is not responding to messages"
- "I want to add a new admin user"
- "How to change session mode"

Claude Code will automatically load the Skill and provide targeted help.

---

## Quick Start

### 1. Prerequisites

- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Discord Bot

#### Create Discord Application

1. Visit [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" to create an app
3. Create a Bot in the "Bot" page and copy the Token
4. In "OAuth2" -> "URL Generator", check:
   - `bot`
   - `messages.read`
   - `messages.write`
5. Use the generated URL to invite Bot to your server

#### Configure Permissions

In the Bot page of Developer Portal:
- **Privileged Gateway Intents**:
  - ✅ Message Content Intent
  - ✅ Server Members Intent (optional)

### 4. Configure Project

Copy and edit the configuration file:

```bash
cd config
copy config.example.yaml config.yaml
notepad config.yaml  # or use other editor
```

Edit `config.yaml`:

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN_HERE"  # Replace with your Token
  command_prefix: "@"
  allowed_channels: []                   # Empty list = all channels
  allowed_users: []                      # Empty list = all users

claude:
  executable: "claude-code"              # Claude Code CLI command
  timeout: 300                           # Timeout (seconds)
  max_retries: 3                         # Max retry count

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                     # Poll interval (ms)
  message_retention_hours: 24            # Message retention time
```

### 5. Start Services

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

Or start the two components separately:

```bash
# Terminal 1: Start Discord Bot
python bot/discord_bot.py

# Terminal 2: Start Claude Bridge service
python bridge/claude_bridge.py
```

### 6. Usage

In Discord:

```
@YourBot Please help me analyze this code
```

The Bot will:
1. Receive the message
2. Show "Message received" confirmation
3. Forward to local Claude Code for processing
4. Send Claude's actual response back to Discord

### 7. Verify Claude Code CLI

Before starting the service, ensure Claude Code CLI is available:

```bash
# Test command
claude -p "Hello, please reply briefly"

# If you see Claude's response, the CLI is properly installed
```

## Configuration Options

### Permission Control

**Restrict specific channels**:
```yaml
allowed_channels: [123456789012345678, 987654321098765432]
```

**Restrict specific users**:
```yaml
allowed_users: [123456789012345678, 987654321098765432]
```

### Claude Code Integration

This project implements real Claude Code CLI calls and supports **continuous conversation**!

**How it works**:
- Uses `claude -p "prompt"` command for non-interactive calls
- Automatically captures Claude's response and returns to Discord
- Supports retry mechanism and timeout control
- **Supports session persistence to maintain conversation context**
- Implements conversation isolation by creating independent working directories for each session

**Optional configuration**:

```yaml
claude:
  executable: "claude"              # Claude CLI command (usually just "claude")
  timeout: 300                       # Single request timeout (seconds)
  max_retries: 3                     # Failure retry count
  working_directory: ""              # Base working directory (optional)
  session_mode: "channel"            # Session mode (see below)
```

**Session mode explanation**:

| Mode | Description | Use Case |
|------|-------------|----------|
| `"channel"` | Each Discord channel has independent session | **Recommended**: Conversations in the same channel maintain context |
| `"user"` | Each user has independent session | User's conversations remain consistent across different channels |
| `"global"` | Globally shared session | Everyone shares the same conversation context |
| `"none"` | New conversation each time | Default mode, no context maintained |

**Working directory explanation**:
- Leave empty (default): Use project root directory
- Set to specific path: Let Claude access specific project files
- Example: `working_directory: "D:/MyProject"`
- Session directories are automatically created in `{working_directory}/sessions/{session_key}/`

**Continuous conversation example**:
```
You: @OH-Bot My name is Zhang San
Bot: ✅ Message received...
Bot: ✨ Response from Claude: Hello Zhang San! Nice to meet you.

You: @OH-Bot What's my name?
Bot: ✅ Message received...
Bot: ✨ Response from Claude: Your name is Zhang San. (Claude remembers the previous conversation!)
```

## Troubleshooting

### Bot Unresponsive

1. Check if Discord Token is correct
2. Confirm Bot has sufficient permissions
3. Confirm Message Content Intent is enabled

### Claude Code Not Responding

1. Test if CLI is available:
   ```bash
   claude -p "test"
   ```
2. Check if Claude Code is logged in:
   ```bash
   claude --version
   ```
3. View detailed error logs in the bridge service window
4. If prompted that claude command is not found:
   - Ensure Claude Code is installed
   - Restart terminal/command window
   - Check PATH environment variable

### Permission Errors

1. Check channel/user IDs in configuration file
2. Confirm Bot has corresponding permissions in the server

## Development Notes

### Message State Flow

```
PENDING -> PROCESSING -> COMPLETED
                |
                v
             FAILED
```

### Database Structure

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    direction TEXT,              -- 'to_claude' or 'to_discord'
    content TEXT,                -- Message content
    status TEXT,                 -- Message status
    discord_channel_id INTEGER,  -- Discord channel ID
    discord_message_id INTEGER,  -- Discord message ID
    discord_user_id INTEGER,     -- User ID
    username TEXT,               -- Username
    response TEXT,               -- Claude's response
    error TEXT,                  -- Error message
    created_at TIMESTAMP,        -- Creation time
    updated_at TIMESTAMP         -- Update time
);
```

## Security Recommendations

- Don't commit `config.yaml` to version control
- Regularly clean message database
- Use restricted user/channel permissions in production
- Use environment variables for sensitive information

## License

MIT License

## Contributing

Issues and Pull Requests are welcome!
