# Discord Claude Bridge

将 Discord 消息桥接到本地 Claude Code CLI 的双向通信系统。

## 功能特性

- ✅ @Bot 调用 Claude Code（支持持续对话）
- ✅ 消息追踪系统（实时状态提示）
- ✅ 会话管理（`/new` 重置、`/status` 状态、`/restart` 重启）
- ✅ 文件下载功能（从 Discord 下载附件到本地）
- ✅ MCP 服务器（Claude Code 可发送文件到 Discord）

## 快速开始

### 1. 前置要求

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

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

**重启服务:**
```bash
restart.bat  # Windows
```

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

## MCP 服务器集成

Claude Code 可通过 MCP 协议发送文件到 Discord。

### 配置方法

**配置文件位置**：
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS/Linux: `~/Library/Application Support/Claude/claude_desktop_config.json`

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

## 配置选项

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

## 故障排查

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

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
