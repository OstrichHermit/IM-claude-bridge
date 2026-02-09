# Discord Claude Bridge

---

<details>
<summary>简体中文版本</summary>

## 简介

将 Discord 消息桥接到本地 Claude Code 的双向通信系统。

## 功能特性

- ✅ 接收 Discord 中 @Bot 的消息
- ✅ 将消息转发给本地 Claude Code CLI
- ✅ 接收 Claude Code 的回复并发送回 Discord
- ✅ 基于消息队列的异步处理
- ✅ 支持权限控制（频道、用户）
- ✅ 消息持久化和状态跟踪
- ✅ 消息追踪系统（实时状态提示）
- ✅ 启动通知功能
- ✅ 会话管理（`/new` 命令重置会话）
- ✅ **MCP 服务器** - 支持 Claude Code 通过 MCP 协议发送文件到 Discord
  - 发送文件到 Discord 用户私聊或频道
  - 批量发送多个文件（最多 10 个）
  - 列出 Bot 可访问的频道和服务器
  - 支持 Embed 精美卡片格式
  - **动态频道解析** - 自动从消息中解析频道 ID，无需手动指定

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
├── mcp_server/
│   ├── server.py           # MCP 服务器主程序
│   ├── tools/              # MCP 工具层
│   │   ├── discord_tools.py  # Discord 文件发送工具
│   │   └── __init__.py
│   └── services/           # MCP 服务层
│       ├── discord_service.py  # Discord 服务实现
│       └── __init__.py
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
├── restart.bat            # Windows 重启脚本
├── start.sh               # Linux/Mac 启动脚本
├── MCP_SETUP.md           # MCP 服务器配置指南
├── claude_desktop_config.example.json  # MCP 配置示例
└── README.md              # 本文件
```

## 🤖 Claude Code Skill

本项目包含一个专门的维护 Skill（`discord-bridge-maintenance`），用于帮助维护和调试 Discord Bridge。

### 安装 Skill

将 Skill 复制到 Claude Code 的 skills 目录：

```bash
# 复制 Skill 到 Claude Code skills 目录
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# Windows 用户
xcopy /E /I docs\skills\discord-bridge-maintenance %USERPROFILE%\.claude\skills\discord-bridge-maintenance
```

**推荐做法**：将项目放在 `/workspace/` 目录下，并将 Skill 复制到工作区的 `.claude/skills/` 目录中，这样可以实现更好的工作区隔离。

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

## 💡 推荐工作区设置

为了完整发挥 Claude Code 的能力并实现良好的工作区隔离，建议按照以下结构设置您的工作区：

### 推荐的目录结构

```
/workspace/                                     # 主工作区（推荐使用此路径）
├── .claude/                                    # Claude Code 配置目录
│   ├── settings.local.json                   # 本地设置（工具使用权限）
│   └── skills/                               # **Skill 目录（重要！）**
│       └── discord-bridge-maintenance/       # 维护 Skill（从项目复制）
└── discord-claude-bridge/                     # 桥接项目（本仓库）
    ├── bot/
    ├── bridge/
    ├── shared/
    │   └── messages.db                       # 消息数据库（运行时生成）
    ├── config/
    └── docs/
        └── skills/
            └── discord-bridge-maintenance/    # Skill 源文件（需要复制到 .claude/skills/）
```

**⚠️ 重要说明**：
- Skill 必须放在 `.claude/skills/` 目录下才能被 Claude Code 自动加载
- 不要直接使用项目中的 `docs/skills/` 目录
- 需要将 Skill 复制到工作区根目录的 `.claude/skills/` 中

### 设置步骤

#### 1. 创建工作区目录

```bash
# Windows (PowerShell)
New-Item -ItemType Directory -Path "/workspace"
Set-Location "/workspace"

# Linux/Mac
sudo mkdir /workspace
cd /workspace
```

#### 2. 克隆项目到工作区

```bash
# 在工作区目录中执行
git clone https://github.com/OstrichHermit/discord-claude-bridge.git
```

#### 3. 复制 Skill 到 Claude Code

```bash
# 在工作区根目录创建 .claude/skills/ 并复制 Skill
mkdir -p .claude/skills
cp -r discord-claude-bridge/docs/skills/discord-bridge-maintenance .claude/skills/

# Windows 用户
xcopy /E /I discord-claude-bridge\docs\skills\discord-bridge-maintenance .claude\skills\discord-bridge-maintenance
```

#### 4. 配置 Claude Code 工具权限

创建 `.claude/settings.local.json` 文件：

```json
{
  "mcpEnabled": true,
  "allowedTools": [
    "bash",
    "editor",
    "computer",
    "browser"
  ],
  "allowedCommands": [
    "python",
    "pip",
    "git",
    "claude"
  ]
}
```

**Windows 用户快速创建配置**：

```powershell
# PowerShell 命令
mkdir .claude
@'
{
  "mcpEnabled": true,
  "allowedTools": ["bash", "editor", "computer", "browser"],
  "allowedCommands": ["python", "pip", "git", "claude"]
}
'@ | Out-File -FilePath .claude\settings.local.json -Encoding utf8
```

### 这样做的好处

- ✅ **完整的工具权限**：Claude Code 可以使用所有必要的工具（Bash、编辑器、浏览器等）
- ✅ **工作区隔离**：桥接项目和会话数据在独立的工作区中，不会影响其他项目
- ✅ **Skill 自动加载**：维护 Skill 在同一工作区，Claude Code 可以自动识别和加载
- ✅ **会话持久化**：所有 Discord 对话的会话数据集中管理
- ✅ **便于维护**：所有相关文件在一个目录中，方便备份和管理
- ✅ **路径简洁**：使用 `/workspace/` 作为根目录，路径更简洁易记

---

## 快速开始

### 1. 前置要求

- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. 推荐的工作区设置

（请参考上方的"推荐工作区设置"章节）

### 3. 安装依赖

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

### 5.1 重启服务

**Windows（推荐）:**
```bash
restart.bat
```

`restart.bat` 脚本会自动：
1. 关闭所有 Discord Bridge 窗口
2. 终止旧的 Python 进程
3. 重新启动 Discord Bot 和 Claude Bridge 服务

**手动重启:**
1. 关闭两个服务窗口（或按 Ctrl+C）
2. 重新运行 `start.bat`

### 6. 使用方法

在 Discord 中：

```
@YourBot 请帮我分析这段代码
```

Bot 会：
1. 接收消息并显示"⏳ 消息已接收"
2. 转发给本地 Claude Code 处理（显示"🔄 正在处理中"）
3. 将 Claude 的真实回复发送回 Discord（显示"✅ 消息 #X 响应成功！"）

**可用命令**：
- `/new` - 开始新的对话上下文（重置会话）
- `/status` - 查看系统状态
- `/restart` - 重启服务

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
- **全局会话模式**：所有对话共享同一个上下文，保持对话连续性
- 使用 `--session-id <uuid>` 参数精确控制会话
- `/new` 命令可重置会话，开始新的对话上下文

**可选配置**：

```yaml
claude:
  executable: "claude"              # Claude CLI 命令（通常就是 "claude"）
  timeout: 300                       # 单次请求超时时间（秒）
  max_retries: 3                     # 失败重试次数
  working_directory: ""              # 工作目录（可选）
```

**工作目录说明**：
- 留空（默认）：使用项目根目录
- 设置为特定路径：让 Claude 可以访问特定项目文件
- 例如：`working_directory: "D:/MyProject"`

**持续对话示例**：
```
你: @OH-Bot 我的名字是张三
Bot: ⏳ 消息已接收...
Bot: 🔄 正在处理中...
Bot: ✨ 来自 Claude 的回复: 你好张三！很高兴认识你。
Bot: ✅ 消息 #X 响应成功！

你: @OH-Bot 我叫什么名字？
Bot: ⏳ 消息已接收...
Bot: 🔄 正在处理中...
Bot: ✨ 来自 Claude 的回复: 你叫张三。（Claude 记住了之前的对话！）
Bot: ✅ 消息 #Y 响应成功！

你: /new
Bot: ✅ 会话已重置！开始新的对话上下文。

你: @OH-Bot 我叫什么名字？
Bot: ⏳ 消息已接收...
Bot: 🔄 正在处理中...
Bot: ✨ 来自 Claude 的回复: 抱歉，我不知道您的名字。（会话已重置，不记得之前的对话）
Bot: ✅ 消息 #Z 响应成功！
```

---

## 🔌 MCP 服务器集成

本项目包含一个 **MCP (Model Context Protocol) 服务器**，允许 Claude Code 通过 MCP 协议直接发送文件到 Discord。

### MCP 功能

通过 MCP 服务器，Claude Code 可以：

- 📎 **发送文件到 Discord** - 支持用户私聊和频道
- 📦 **批量发送文件** - 一次最多发送 10 个文件
- 📋 **列出频道** - 查看 Bot 可访问的所有频道和服务器
- 🎨 **Embed 格式** - 使用精美的卡片格式发送内容
- 🎯 **自动识别频道** - 从消息格式中自动解析频道 ID

### 可用工具

MCP 服务器提供以下 3 个工具：

1. **`mcp_send_file_to_discord`** - 发送单个文件到 Discord
   - 支持发送到用户私聊或频道
   - 可选 Embed 精美格式

2. **`mcp_send_multiple_files_to_discord`** - 批量发送文件到 Discord
   - 一次最多发送 10 个文件
   - 自动跳过不存在的文件

3. **`mcp_list_discord_channels`** - 列出 Bot 可访问的频道
   - 返回所有可访问的服务器和频道信息

### 快速配置

#### 1. 编辑 Claude Code 配置文件

配置文件位置：

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS/Linux:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

#### 2. 添加 MCP 服务器

```json
{
  "mcpServers": {
    "discord-bridge": {
      "command": "python",
      "args": [
        "D:\\AgentWorkspace\\discord-claude-bridge\\mcp_server\\server.py",
        "--transport",
        "stdio"
      ],
      "env": {
        "PYTHONPATH": "D:\\AgentWorkspace\\discord-claude-bridge"
      }
    }
  }
}
```

**提示：** 可以参考项目根目录的 `claude_desktop_config.example.json` 文件。

#### 3. 重启 Claude Code

完全关闭并重新启动 Claude Code 应用。

### 使用示例

配置完成后，在 Claude Code 中可以直接发送文件到 Discord：

#### 示例 1：发送到当前频道（自动解析）⭐

当你在 Discord 频道中与 Claude 对话时，它可以自动识别当前频道并发送文件：

```
你（在 Discord 频道中）：请把根目录下的新闻汇总 PDF 发过来
Claude：好的，正在发送...
[自动识别频道 ID 并发送文件]
```

**工作原理**：
- Discord Bot 转发消息时包含频道 ID：`来自频道（1466858871720251425）的鸵鸟居士说：请把根目录下的新闻汇总 PDF 发过来`
- Claude Code 从消息中解析频道 ID
- 调用 MCP 工具发送文件到该频道

#### 示例 2：指定频道发送

```
你：请将 D:\charts\sales.png 发送到 Discord 频道 123456789
```

#### 示例 3：发送到用户私聊

```
你：把这个文件发给用户 987654321
```

#### 示例 4：批量发送

```
你：将这些图片打包发送：image1.png, image2.png
```

#### 示例 5：使用精美格式

```
你：用卡片格式发送报告到我的私聊
```

### MCP 工具列表

- `mcp__discord-bridge__mcp_send_file_to_discord` - 发送单个文件
- `mcp__discord-bridge__mcp_send_multiple_files_to_discord` - 批量发送文件（最多 10 个）
- `mcp__discord-bridge__mcp_list_discord_channels` - 列出可访问的频道

### 详细文档

完整的 MCP 配置和使用指南，请参阅：

**[MCP_SETUP.md](MCP_SETUP.md)** - Discord Bridge MCP 服务器配置指南

包含内容：
- 详细的配置步骤
- 所有 MCP 工具说明
- 故障排查指南
- 安全建议
- 高级配置选项

---

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

## 安全建议

- 不要提交 `config.yaml` 到版本控制
- 定期清理消息数据库
- 在生产环境使用受限的用户/频道权限
- 使用环境变量存储敏感信息

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

</details>

---

<details>
<summary>English version</summary>

## Introduction

A bidirectional communication system that bridges Discord messages to your local Claude Code CLI.

## Features

- ✅ Receive @Bot messages from Discord
- ✅ Forward messages to local Claude Code CLI
- ✅ Receive Claude Code responses and send back to Discord
- ✅ Async processing based on message queue
- ✅ Support permission control (channels, users)
- ✅ Message persistence and status tracking
- ✅ Message tracking system (real-time status updates)
- ✅ Startup notification feature
- ✅ Session management (`/new` command to reset session)

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
├── restart.bat            # Windows restart script
├── start.sh               # Linux/Mac startup script
└── README.md              # This file
```

## 🤖 Claude Code Skill

This project includes a dedicated maintenance Skill (`discord-bridge-maintenance`) to help you maintain and debug Discord Bridge.

### Install Skill

Copy the Skill to Claude Code's skills directory:

```bash
# Copy Skill to Claude Code skills directory
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# Windows users
xcopy /E /I docs\skills\discord-bridge-maintenance %USERPROFILE%\.claude\skills\discord-bridge-maintenance
```

**Recommended**: Place the project in `/workspace/` directory and copy the Skill to the workspace's `.claude/skills/` directory for better workspace isolation.

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

## 💡 Recommended Workspace Setup

To fully leverage Claude Code capabilities and achieve proper workspace isolation, we recommend setting up your workspace as follows:

### Recommended Directory Structure

```
/workspace/                                     # Main workspace (recommended path)
├── .claude/                                    # Claude Code config directory
│   ├── settings.local.json                   # Local settings (tool permissions)
│   └── skills/                               # **Skill directory (important!)**
│       └── discord-bridge-maintenance/       # Maintenance Skill (copy from project)
└── discord-claude-bridge/                     # Bridge project (this repo)
    ├── bot/
    ├── bridge/
    ├── shared/
    │   └── messages.db                       # Message database (generated at runtime)
    ├── config/
    └── docs/
        └── skills/
            └── discord-bridge-maintenance/    # Skill source files (copy to .claude/skills/)
```

**⚠️ Important**:
- Skill MUST be placed in `.claude/skills/` directory to be auto-loaded by Claude Code
- Do NOT use the `docs/skills/` directory in the project directly
- Need to copy the Skill to `.claude/skills/` in the workspace root directory

### Setup Steps

#### 1. Create Workspace Directory

```bash
# Windows (PowerShell)
New-Item -ItemType Directory -Path "/workspace"
Set-Location "/workspace"

# Linux/Mac
sudo mkdir /workspace
cd /workspace
```

#### 2. Clone Project to Workspace

```bash
# Execute in workspace directory
git clone https://github.com/OstrichHermit/discord-claude-bridge.git
```

#### 3. Copy Skill to Claude Code

```bash
# Create .claude/skills/ in workspace root and copy Skill
mkdir -p .claude/skills
cp -r discord-claude-bridge/docs/skills/discord-bridge-maintenance .claude/skills/

# Windows users
xcopy /E /I discord-claude-bridge\docs\skills\discord-bridge-maintenance .claude\skills\discord-bridge-maintenance
```

#### 4. Configure Claude Code Tool Permissions

Create `.claude/settings.local.json` file:

```json
{
  "mcpEnabled": true,
  "allowedTools": [
    "bash",
    "editor",
    "computer",
    "browser"
  ],
  "allowedCommands": [
    "python",
    "pip",
    "git",
    "claude"
  ]
}
```

**Quick Setup for Windows Users**:

```powershell
# PowerShell command
mkdir .claude
@'
{
  "mcpEnabled": true,
  "allowedTools": ["bash", "editor", "computer", "browser"],
  "allowedCommands": ["python", "pip", "git", "claude"]
}
'@ | Out-File -FilePath .claude\settings.local.json -Encoding utf8
```

### Benefits

- ✅ **Full Tool Permissions**: Claude Code can use all necessary tools (Bash, Editor, Browser, etc.)
- ✅ **Workspace Isolation**: Bridge project and session data in independent workspace, won't affect other projects
- ✅ **Auto-load Skill**: Maintenance Skill in same workspace, Claude Code can automatically recognize and load it
- ✅ **Session Persistence**: All Discord conversation session data centrally managed
- ✅ **Easy Maintenance**: All related files in one directory, easy to backup and manage
- ✅ **Clean Path**: Using `/workspace/` as root makes paths simple and easy to remember

---

## Quick Start

### 1. Prerequisites

- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. Recommended Workspace Setup

(Please refer to "Recommended Workspace Setup" section above)

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Discord Bot

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

### 5.1 Restart Services

**Windows (Recommended):**
```bash
restart.bat
```

The `restart.bat` script will automatically:
1. Close all Discord Bridge windows
2. Terminate old Python processes
3. Restart Discord Bot and Claude Bridge services

**Manual Restart:**
1. Close both service windows (or press Ctrl+C)
2. Re-run `start.bat`

### 6. Usage

In Discord:

```
@YourBot Please help me analyze this code
```

The Bot will:
1. Receive message and show "⏳ Message received"
2. Forward to local Claude Code for processing (show "🔄 Processing")
3. Send Claude's actual response back to Discord (show "✅ Message #X responded successfully!")

**Available commands**:
- `/new` - Start new conversation context (reset session)
- `/status` - View system status
- `/restart` - Restart service

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
- **Global session mode**: All conversations share the same context for continuity
- Uses `--session-id <uuid>` parameter for precise session control
- `/new` command resets session to start fresh conversation context

**Optional configuration**:

```yaml
claude:
  executable: "claude"              # Claude CLI command (usually just "claude")
  timeout: 300                       # Single request timeout (seconds)
  max_retries: 3                     # Failure retry count
  working_directory: ""              # Working directory (optional)
```

**Working directory explanation**:
- Leave empty (default): Use project root directory
- Set to specific path: Let Claude access specific project files
- Example: `working_directory: "D:/MyProject"`

**Continuous conversation example**:
```
You: @OH-Bot My name is Zhang San
Bot: ⏳ Message received...
Bot: 🔄 Processing...
Bot: ✨ Response from Claude: Hello Zhang San! Nice to meet you.
Bot: ✅ Message #X responded successfully!

You: @OH-Bot What's my name?
Bot: ⏳ Message received...
Bot: 🔄 Processing...
Bot: ✨ Response from Claude: Your name is Zhang San. (Claude remembers the previous conversation!)
Bot: ✅ Message #Y responded successfully!

You: /new
Bot: ✅ Session reset! Starting new conversation context.

You: @OH-Bot What's my name?
Bot: ⏳ Message received...
Bot: 🔄 Processing...
Bot: ✨ Response from Claude: Sorry, I don't know your name. (Session reset, no memory of previous conversation)
Bot: ✅ Message #Z responded successfully!
```

---

## 🔌 MCP Server Integration

This project includes an **MCP (Model Context Protocol) server** that allows Claude Code to send files directly to Discord through the MCP protocol.

### MCP Features

Through the MCP server, Claude Code can:

- 📎 **Send files to Discord** - Support user DM and channels
- 📦 **Batch send files** - Send up to 10 files at once
- 📋 **List channels** - View all channels and servers accessible by the Bot
- 🎨 **Embed format** - Send content in beautiful card format
- 🎯 **Auto-recognize channel** - Automatically parse channel ID from message format

### Available Tools

The MCP server provides the following 3 tools:

1. **`mcp_send_file_to_discord`** - Send single file to Discord
   - Support sending to user DM or channel
   - Optional Embed beautiful format

2. **`mcp_send_multiple_files_to_discord`** - Batch send files to Discord
   - Send up to 10 files at once
   - Automatically skip non-existent files

3. **`mcp_list_discord_channels`** - List Bot accessible channels
   - Return all accessible servers and channel information

### Quick Configuration

#### 1. Edit Claude Code Configuration File

Configuration file location:

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS/Linux:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

#### 2. Add MCP Server

```json
{
  "mcpServers": {
    "discord-bridge": {
      "command": "python",
      "args": [
        "D:\\AgentWorkspace\\discord-claude-bridge\\mcp_server\\server.py",
        "--transport",
        "stdio"
      ],
      "env": {
        "PYTHONPATH": "D:\\AgentWorkspace\\discord-claude-bridge"
      }
    }
  }
}
```

**Tip:** You can refer to the `claude_desktop_config.example.json` file in the project root directory.

#### 3. Restart Claude Code

Completely close and restart the Claude Code application.

### Usage Examples

After configuration, you can send files directly to Discord in Claude Code:

#### Example 1: Auto-recognize Channel (Recommended)

```
You (in Discord channel): Please send the news summary PDF from root directory
Claude: OK, sending...
[Automatically recognize channel ID and send file]
```

**How it works**:
- Discord Bot includes channel ID when forwarding message: `From channel (1466858871720251425) OstrichHermit said: Please send the news summary PDF from root directory`
- Claude Code parses channel ID from message
- Call MCP tool to send file to that channel

#### Example 2: Specify Channel

```
You: Please send D:\charts\sales.png to Discord channel 123456789
```

#### Example 3: Send to User DM

```
You: Send this file to user 987654321
```

#### Example 4: Batch Send

```
You: Send these images in batch: image1.png, image2.png
```

#### Example 5: Use Beautiful Format

```
You: Send report to my DM in card format
```

### MCP Tool List

- `mcp__discord-bridge__mcp_send_file_to_discord` - Send single file
- `mcp__discord-bridge__mcp_send_multiple_files_to_discord` - Batch send files (up to 10)
- `mcp__discord-bridge__mcp_list_discord_channels` - List accessible channels

### Detailed Documentation

For complete MCP configuration and usage guide, please refer to:

**[MCP_SETUP.md](MCP_SETUP.md)** - Discord Bridge MCP Server Configuration Guide

Includes:
- Detailed configuration steps
- All MCP tool descriptions
- Troubleshooting guide
- Security recommendations
- Advanced configuration options

---

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

## Security Recommendations

- Don't commit `config.yaml` to version control
- Regularly clean message database
- Use restricted user/channel permissions in production
- Use environment variables for sensitive information

## License

MIT License

## Contributing

Issues and Pull Requests are welcome!

</details>
