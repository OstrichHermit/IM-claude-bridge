# Discord Claude Bridge

将 Discord 消息桥接到本地 Claude Code CLI 的双向通信系统。

A two-way communication system that bridges Discord messages to your local Claude Code CLI.

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ 核心功能

**消息交互**
- ✅ @Bot 调用本地 Claude Code CLI
- ✅ 持续对话支持（会话管理）
- ✅ 实时状态反馈（接收 → 处理 → 响应）
- ✅ 消息追踪系统（避免重复处理）

**文件传输**
- ✅ 从 Discord 下载附件到本地
- ✅ 通过 MCP 发送文件到 Discord
- ✅ 批量文件传输支持

**服务管理**
- ✅ Windows 守护进程（自动监控重启）
- ✅ Discord 斜杠命令控制（`/new`、`/status`、`/restart`、`/stop`）
- ✅ 消息队列系统（SQLite 持久化）

## 🚀 快速开始

### 0. 推荐工作区结构

**强烈建议**将本项目放在 Claude Code 的工作区根目录下，方便管理多个项目。

**示例结构**：
```
D:/AgentWorkspace/                    # 工作区根目录
├── discord-claude-bridge/            # Discord 桥接项目（本仓库）
├── my-project-1/                     # 你的其他项目
├── downloads/                        # 默认文件下载目录
└── .claude/                          # Claude Code 配置
    └── skills/                       # 维护 Skill 目录
        └── discord-bridge-maintenance/  # 本项目的维护 Skill
```

**维护 Skill 使用**（推荐安装）：
```bash
# 复制维护 Skill 到 Claude Code 配置目录
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# 复制定时任务 Skill（可选，用于创建定时提醒）
cp -r docs/skills/scheduler-task ~/.claude/skills/
```

**discord-bridge-maintenance Skill 功能**：
- 🔧 查看系统架构和配置说明
- 📊 监控消息队列和下载状态
- 🐛 快速故障排查（Bot 无响应、下载超时等）
- 📝 查看数据库记录（消息、下载请求）
- 🔄 查看待处理任务列表

**scheduler-task Skill 功能**（定时提醒）：
- ⏰ 创建和管理 Windows 计划任务
- 📝 编写异步执行的 bat 脚本（避免任务阻塞）
- 🎯 配置 Discord Bridge 命令参数（支持私聊和频道）
- 🔧 正确处理中文字符编码问题
- 📅 实现定时提醒、日程通知、任务报告等自动化功能

**典型应用场景**：
- 每天定时提醒（刷牙、休息、喝水等健康提醒）
- 定时报告（每小时/每天发送状态报告）
- 日程通知（会议提醒、任务截止提醒）
- 自动化工作流（定时执行脚本并发送结果通知）

### 1. 前置要求

- Windows 系统
- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. 安装

```bash
# 克隆项目
git clone https://github.com/OstrichHermit/discord-claude-bridge.git
cd discord-claude-bridge

# 安装依赖
pip install -r requirements.txt

# 配置 Discord Bot Token
cp config/config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 Discord Bot Token
```

### 3. 创建 Discord 应用

访问 [Discord Developer Portal](https://discord.com/developers/applications)：

1. 创建应用 → Bot 页面 → 创建 Bot → 复制 Token
2. OAuth2 → URL Generator → 勾选 `bot`、`messages.read`、`messages.write`
3. Bot 页面 → **Privileged Gateway Intents** → 启用 **Message Content Intent**
4. 使用生成的 URL 邀请 Bot 到服务器

### 4. 启动服务

**一键启动**（推荐）：
```bash
start.bat
```

> Manager 守护进程会自动启动并监控所有服务（Bot + Bridge）


### 5. 使用方法

#### 5.1 基本 Chat

在 Discord 中 @Bot 即可：

```
@YourBot 请帮我分析这段代码
```

Bot 会：
1. 接收消息并显示"⏳ 消息已接收"
2. 转发给本地 Claude Code 处理（显示"🔄 正在处理中"）
3. 将 Claude 的回复发送回 Discord（显示"✅ 消息 #X 响应成功！"）

#### 5.2 斜杠命令

- `/new` - 重置会话，开始新的对话上下文
- `/status` - 查看系统状态（会话 ID、数据库统计等）
- `/restart` - 重启服务
- `/stop` - 停止服务

#### 5.3 文件下载

回复带有附件的消息，@Bot 并指定目录：

```
# 使用默认目录（D:/AgentWorkspace/downloads）
@YourBot 下载

# 指定目录
@YourBot 下载到 D:/myfiles

# 英文格式
@YourBot save D:/downloads

# 直接路径
@YourBot D:/AgentWorkspace/files
```

**下载特性**：
- ✅ 支持所有附件类型（图片、文档、压缩包等）
- ✅ 批量下载（一条消息多个附件）
- ✅ 自动处理文件名冲突（自动重命名）
- ✅ 实时进度提示（每 30 秒更新一次）

**配置默认目录**（在 `config.yaml`）：
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/downloads"
```

## 🔌 MCP 服务器集成

Claude Code 可通过 MCP 协议发送文件到 Discord。

### 配置方法

**配置文件位置**：`%APPDATA%\Claude\claude_desktop_config.json`

**添加 MCP 服务器**：
```json
{
  "mcpServers": {
    "discord-bridge": {
      "command": "python",
      "args": [
        "D:\\AgentWorkspace\\discord-claude-bridge\\mcp_server\\server.py",
        "--transport", "stdio"
      ],
      "env": {
        "PYTHONPATH": "D:\\AgentWorkspace\\discord-claude-bridge"
      }
    }
  }
}
```

### MCP 工具

1. **发送文件到 Discord** - 支持用户私聊和频道
2. **批量发送文件** - 一次最多 10 个文件
3. **列出频道** - 查看 Bot 可访问的所有频道

详细配置请参考：[MCP_SETUP.md](MCP_SETUP.md)

## ⚙️ 配置选项

### config.yaml 主要配置

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"  # Discord Bot Token
  command_prefix: "@"                  # 命令前缀
  allowed_channels: []                # 允许的频道（空 = 所有）
  allowed_users: []                   # 允许的用户（空 = 所有）

claude:
  executable: "claude"                 # Claude Code CLI 命令
  timeout: 300                         # 超时时间（秒）
  max_retries: 3                       # 最大重试次数
  working_directory: ""               # 工作目录（可选）

file_download:
  default_directory: "D:/AgentWorkspace/downloads"  # 默认下载目录

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                   # 轮询间隔（毫秒）
  message_retention_hours: 24          # 消息保留时间
```

## 🔧 故障排查

### Bot 无响应

1. 检查 Discord Token 是否正确
2. 确认 Bot 有足够权限
3. 确认已启用 Message Content Intent

### Claude Code 未响应

1. 测试 CLI：`claude -p "test"`
2. 检查是否登录：`claude --version`
3. 查看桥接服务窗口的错误日志

### 下载超时

- 已修复：使用轮询检查状态（每 2 秒）
- 大文件可能需要更长时间，请耐心等待
- 如一直超时，检查 Bot 进程是否运行

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
