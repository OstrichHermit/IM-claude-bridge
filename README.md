# IM Claude Bridge

将 Discord/微信消息桥接到本地 Claude Code CLI 的双向通信系统。

A two-way communication system that bridges Discord/WeChat messages to your local Claude Code CLI.

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ 核心功能

**💬 消息交互**
- `@Bot`调用本地 Claude Code CLI（支持 Discord 和微信）
- 持续对话支持（会话管理）
- 实时状态反馈（接收 → 处理 → 响应）
- 消息追踪系统（避免重复处理）
- 使用消息队列机制统一处理响应（保证消息顺序正确）
- 工具调用通知（在 Discord 中以 Embed 卡片形式发送）
- 图片表情包（自动检测 `<:文件名.扩展名>` 格式并发送图片）

**📁 文件传输**
- 支持所有文件类型（图片、文档、压缩包等）
- 在 Discord 中发送消息时附带文件（自动下载并传入文件信息）
- 在 Discord 中右键上下文菜单下载文件
- 引用文件信息（自动提取文件信息发送给 Claude Code）
- 自动从 Discord/微信下载文件到本地配置的目录
- 自动处理文件名冲突（自动重命名）
- 通过 MCP 发送文件到 Discord/微信
- 批量文件传输支持

**⏰ 定时任务**
- 创建和管理定时任务（支持 cron 表达式）
- 支持私聊和频道消息推送
- 任务启用/禁用/更新/删除
- 执行历史记录查询

**🎛️ Web 控制界面**
- 实时监控各组件运行状态（Discord Bot / Weixin Bot / Bridge / Manager / MCP Server）
- 实时查看日志输出（5 个组件独立日志面板）
- 深色/浅色主题切换
- 一键重启/停止所有服务
- 自动重连 WebSocket 连接

**🎯 服务管理**
- Windows 守护进程（自动监控重启）
- Discord 斜杠命令控制（`/new`、`/status`、`/restart`、`/stop`、`/abort`、`/mention`）
- 上下文菜单（右键消息下载附件）
- 消息队列系统（SQLite 持久化）
- **并行 Bot 架构**（Discord Bot 和微信 Bot 独立运行）

## 🚀 快速开始

### 0. 推荐工作区结构

**强烈建议**将本项目放在 Claude Code 的工作区根目录下，方便管理多个项目。

**示例结构**：
```
D:/AgentWorkspace/                    # 工作区根目录
├── IM-claude-bridge/                 # IM 桥接项目（本仓库）
├── my-project-1/                     # 你的其他项目
├── downloads/                        # 默认文件下载目录
└── .claude/                          # Claude Code 配置
```

### 1. 前置要求

- Windows 系统
- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. 安装

```bash
# 克隆项目
git clone https://github.com/OstrichHermit/IM-claude-bridge.git
cd IM-claude-bridge

# 安装依赖
pip install -r requirements.txt

# 配置 Discord Bot Token
cp config/config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 Discord Bot Token
```

### 3. 创建 Discord 应用

访问 [Discord Developer Portal](https://discord.com/developers/applications)：

1. 创建应用 → Bot 页面 → 创建 Bot → 复制 Token
2. OAuth2 → URL Generator：
   - **Scopes** 勾选：`bot` + `applications.commands`
   - **Bot Permissions** 勾选：
     - `Send Messages`（发送消息）
     - `Read Messages/View Channels`（查看消息）
     - `Embed Links`（发送链接卡片）
     - `Attach Files`（发送文件）
     - `Add Reactions`（添加表情）
     - `Use Slash Commands`（使用斜杠命令）
3. Bot 页面 → **Privileged Gateway Intents** → 启用 **Message Content Intent**
4. 复制生成的 URL，在浏览器打开 → 选择服务器 → 授权

> 💡 **提示**：同一个 Bot 可以被邀请到多个服务器，直接用相同的链接再次访问即可。

### 4. 启动服务

**一键启动**（推荐）：
```bash
start.bat
```

> Manager 守护进程会自动启动并监控所有服务（Discord Bot + Weixin Bot + Bridge + Web Server）

启动后访问 **Web 控制界面**：http://localhost:8088（默认值，可在 `config.yaml` 中修改）

在 Web 界面中你可以：
- 实时查看各组件运行状态和 PID
- 查看实时日志输出（4 个组件独立面板）
- 切换深色/浅色主题
- 一键重启/停止所有服务


### 5. 使用方法

### 5.1 基本对话

在 Discord 中`@Bot`并发送消息：

```
@YourBot 请帮我分析这段代码
```

在微信中直接发送消息：

```
请帮我分析这段代码
```

Bot 会接收消息并显示正在输入，并在响应完成后停止。

### 5.2 斜杠命令

- `/new` - 重置会话，开始新的对话上下文
- `/status` - 查看系统状态（会话 ID、数据库统计等）
- `/abort` - 中止当前正在处理的输出
- `/mention` - 切换当前频道是否需要 @机器人 才能触发对话（每个频道独立管理）
- `/restart` - 重启服务
- `/stop` - 停止服务

**Discord 上下文菜单**（右键消息）：
- **下载附件** - 右键点击带附件的消息 → Apps → 下载附件

### 5.3 图片表情包

Bot 支持自动发送图片表情包，让对话更生动自然。

**表情包目录**：`stickers/`

**发送格式**：`<:文件名.扩展名>`

**示例**：
```
<:开心-森贝儿贵宾犬起飞.gif>
<:疑惑-小巫母鸡蹲.png>
<:无语.gif>
```

**使用方式**：
- Claude Code 在回复中包含 `<:文件名.扩展名>` 格式
- Bot 自动检测并替换为对应表情包图片
- 表情包会按在文本中的位置拆分发送，文本和表情包交替出现

**表情包命名规范**：
- 格式：`含义-内容.扩展名`
- 示例：`开心-森贝儿贵宾犬起飞.gif`

### 5.4 文件操作

**配置默认下载目录**（在 `config.yaml`）：
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/files/downloads"
```

**Discord 方式一：发送消息时附带附件**

直接在`@Bot`消息中附带文件，Bot 会自动下载并传入文件信息，例如：

```
[@YourBot 同时上传文件]
@YourBot 帮我分析这些文件
```

**Discord 方式二：下载附件（上下文菜单）**

如果你发送了带附件的消息但忘记`@Bot`，可以右键点击那条消息：

1. 右键点击带附件的消息
2. 选择 **Apps** → **下载附件**
3. Bot 自动下载所有附件到配置目录

**微信方式：发送文件消息**

直接给 Bot 发送文件消息，Bot 会自动下载。

**Discord/微信通用方式：引用文件信息**

回复一条带有文件的消息并`@Bot`（微信中不需要`@`），Bot 会提取附件信息（元数据）发送给 Claude Code，但**不下载文件**，例如：

```
[回复一条有图片的消息]
@YourBot 帮我分析这张图片
```

**适用场景：**
- 让 Bot 查看指定文件
- 文件已存在本地，只需引用

**与其他方式的区别：**
- 其他方式会下载文件到本地
- 通用方式只提取文件信息，不下载

## 🔌 MCP 服务器集成

Claude Code 可通过 MCP 协议发送文件到 Discord/微信，并管理定时任务。

### HTTP 模式（独立运行，推荐）

HTTP 模式下，MCP 服务器作为后台服务运行，可通过 Web 界面监控状态。

**配置文件位置**：`%APPDATA%\Claude\claude_desktop_config.json`

**添加 MCP 服务器**：
```json
{
  "mcpServers": {
    "im-claude-bridge": {
      "type": "http",
      "url": "http://127.0.0.1:3336/mcp"
    }
  }
}
```

**Web 界面监控**：

MCP 服务器会随 `start.bat` / `restart.bat` 自动启动，访问 Web 控制界面可实时监控 MCP Server 状态和日志。

### MCP 工具

**Discord 文件传输**：
1. **send_file_to_discord** - 发送文件到 Discord（支持私聊/频道）
2. **send_multiple_files_to_discord** - 批量发送文件到 Discord（最多10个）

**微信文件传输**：
1. **send_file_to_weixin** - 发送文件到微信（支持私聊/群聊）
2. **send_multiple_files_to_weixin** - 批量发送文件到微信（最多9个）

**定时任务**：
1. **add_cron** - 添加定时任务（支持 cron 表达式）
2. **list_cron** - 列出所有定时任务
3. **delete_cron** - 删除定时任务
4. **toggle_cron** - 启用/禁用定时任务
5. **get_cron_info** - 获取定时任务详情
6. **update_cron** - 更新定时任务
7. **get_current_time** - 获取当前时间（支持多时区）


## ⚙️ 配置选项

### config.yaml 主要配置

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"  # Discord Bot Token
  allowed_channels: []                # 允许的频道（空 = 所有）
  allowed_users: []                   # 允许的用户（空 = 所有）
  mention_required: true              # 新频道的默认设置（可通过 /mention 按频道独立切换）

claude:
  executable: "claude"                 # Claude Code CLI 命令
  timeout: 300                         # 超时时间（秒）
  max_attempts: 3                      # 最大调用尝试次数（包括第一次）
  working_directory: ""               # 工作目录（可选）
  max_concurrent_sessions: 5           # 最大并发 session 数（0 = 无限制）
  worker_idle_timeout: 300             # Worker 空闲超时时间（秒，0 = 永不清理）

file_download:
  default_directory: "D:/AgentWorkspace/downloads"  # 默认下载目录

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                   # 轮询间隔（毫秒）
  message_retention_hours: 24          # 消息保留时间
  send_interval: 1.5                   # 消息发送间隔（秒）

message_splitting:
  enabled: true                        # 是否启用消息按空行分割功能（让回复更自然拟人）

auto_load:
  enabled: true                        # 首次对话提示词注入（会添加到新会话第一条消息前面）
  prompt_text: "先仔细阅读并遵守 CLAUDE.md..."  # 提示词注入文本

auto_trigger_before_new:
  enabled: false                       # /new 前自动发送提示词（让 Claude 在旧会话中做最后处理，如保存记忆）
  prompt_text: "会话即将重置，请保存或更新重要记忆。"  # 自动发送的预设用户消息

auto_trigger_after_new:
  enabled: true                        # /new 后自动触发对话
  prompt_text: "执行会话启动流程。"      # 自动发送的预设用户消息

typing_indicator:
  max_retries: 3                       # 最大连续重试次数（网络波动时）
  retry_delay: 3                       # 重试等待时间（秒）

timeout:
  pending: 30                          # PENDING 状态超时时间（秒）

tool_use_notification:
  enabled: true                        # 是否启用工具调用通知（以 Embed 卡片形式转发）

# 微信 Bot 配置（可选）
weixin:
  enabled: false                       # 是否启用微信 Bot
  accounts_file: "./config/weixin_accounts.json"  # 微信账号存储文件路径
```

### 微信 Bot 配置（可选）

如果需要使用微信支持，按照以下步骤配置：

#### 1. 微信扫码登录

**方式一：交互式登录（首次添加账号）**

```bash
python scripts/login_weixin.py
```

在终端扫描二维码登录微信。可以多次执行此命令添加多个微信账号。

**方式二：快速重新登录（已有账号 token 失效时）**

```bash
# 第一步：获取二维码链接
python scripts/get_weixin_qrcode.py

# 第二步：用微信扫描二维码后，轮询登录状态并更新配置
python scripts/poll_weixin_login.py <qrcode_id> <username>
# 示例：python scripts/poll_weixin_login.py 51c0f8844acb0552fe6a3741545802fb 猪猪大王
```

指定 `username` 会自动更新 `weixin_accounts.json` 中对应用户的 bot_id 和 bot_token，保留原有的用户名、user_id 等配置。不指定 `username` 则仅打印登录信息。

#### 2. 启用微信 Bot

在 `config.yaml` 中启用微信 Bot：

```yaml
weixin:
  enabled: true
```

#### 3. 启动服务

微信 Bot 会自动随 Discord Bot 一起启动，两者互不影响。

## 🔧 故障排查

### Web 界面无法访问

1. 检查 Web Server 是否运行：访问 Web 控制界面
2. 查看服务状态：在 Web 界面左侧查看各组件状态
3. 检查端口占用，确保端口未被占用

### Bot 无响应

1. 检查 Discord Token 是否正确
2. 确认 Bot 有足够权限
3. 确认已启用 Message Content Intent
4. 在 Web 界面查看实时日志排查问题

### Claude Code 未响应

1. 测试 CLI：`claude -p "test"`
2. 检查是否登录：`claude --version`
3. 在 Web 界面查看 Bridge 日志
4. 检查工作目录配置是否正确

### 下载超时

- 已修复：使用轮询检查状态（每 2 秒）
- 大文件可能需要更长时间，请耐心等待
- 如一直超时，在 Web 界面查看 Bot 进程是否运行

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
