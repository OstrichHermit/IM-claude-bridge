# IM Claude Bridge

A two-way communication system that bridges Discord/WeChat messages to your local Claude Code CLI.

将 Discord/微信消息桥接到本地 Claude Code CLI 的双向通信系统。

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ Features

**💬 Message Interaction**
- `@Bot` to call local Claude Code CLI (supports Discord and WeChat)
- Continuous conversation support (session management)
- Real-time status feedback (received → processing → response)
- Message tracking system (avoid duplicate processing)
- Unified message queue mechanism for all responses (guarantee correct message order)
- Tool use notification (forward as Embed cards in Discord)
- Image Stickers (auto-detect `<:filename.extension>` format and send images)

**📁 File Transfer**
- Support all file types (images, documents, archives, etc.)
- Send attachments with messages in Discord (auto download and pass attachment info)
- Download attachments via Discord context menu (right-click)
- Reference attachment metadata (extract attachment info and send to Claude)
- Auto download attachments from Discord/WeChat to local configured directory
- Auto handle filename conflicts (auto rename)
- Send files to Discord/WeChat via MCP
- Batch file transfer support

**⏰ Scheduled Tasks**
- Create and manage scheduled tasks (supports cron expressions)
- Support for DM and channel message pushing
- Task enable/disable/update/delete
- Execution history query

**🎛️ Web Control Panel**
- Real-time monitoring of component status (Discord Bot / Weixin Bot / Bridge / Manager / MCP Server)
- Real-time log viewing (5 independent component log panels)
- Dark/Light theme toggle
- One-click restart/stop all services
- Auto-reconnect WebSocket connections

**🎯 Service Management**
- Windows daemon process (auto monitor & restart)
- Discord slash commands (`/new`, `/status`, `/abort`, `/restart`, `/stop`, `/mention`, `/split`)
- Context menus (right-click message to download attachments)
- Message queue system (SQLite persistence)
- **Parallel Bot Architecture** (Discord Bot and WeChat Bot run independently)

## 🚀 Quick Start

### 0. Recommended Workspace Structure

**Highly recommended** to place this project in the root directory of your Claude Code workspace for easier management of multiple projects.

**Example structure**:
```
D:/AgentWorkspace/                    # Workspace root
├── IM-claude-bridge/                 # IM bridge project (this repo)
├── my-project-1/                     # Your other projects
├── downloads/                        # Default download directory
└── .claude/                          # Claude Code configuration
```

### 1. Prerequisites

- Windows system
- Python 3.8+
- Discord Bot Token
- Claude Code CLI

### 2. Installation

```bash
# Clone the project
git clone https://github.com/OstrichHermit/IM-claude-bridge.git
cd IM-claude-bridge

# Install dependencies
pip install -r requirements.txt

# Configure Discord Bot Token
cp config/config.example.yaml config.yaml
# Edit config.yaml and enter your Discord Bot Token
```

### 3. Create Discord Application

Visit [Discord Developer Portal](https://discord.com/developers/applications):

1. Create Application → Bot → Create Bot → Copy Token
2. OAuth2 → URL Generator:
   - **Scopes**: Check `bot` + `applications.commands`
   - **Bot Permissions**: Check
     - `Send Messages` (Send messages)
     - `Read Messages/View Channels` (View messages)
     - `Embed Links` (Send link cards)
     - `Attach Files` (Send files)
     - `Add Reactions` (Add emojis)
     - `Use Slash Commands` (Use slash commands)
3. Bot page → **Privileged Gateway Intents** → Enable **Message Content Intent**
4. Copy the generated URL, open in browser → Select server → Authorize

> 💡 **Tip**: The same Bot can be invited to multiple servers. Just visit the same URL again and select a different server.

### 4. Start Service

**One-click start** (recommended):
```bash
start.bat
```

> Manager daemon will automatically start and monitor all services (Discord Bot + Weixin Bot + Bridge + Web Server)

After starting, visit the **Web Control Panel** (default address: http://localhost:8088, can be modified in `config.yaml`)

In the Web Panel you can:
- View real-time status and PID of each component
- View real-time logs (4 independent component panels)
- Toggle dark/light theme
- One-click restart/stop all services


### 5. Usage

### 5.1 Basic Chat

In Discord, `@Bot` and send a message:

```
@YourBot Please help me analyze this code
```

In WeChat, send a message directly:

```
Please help me analyze this code
```

The bot will receive messages and display that it is typing, and then stop when the response is complete.

### 5.2 Slash Commands

- `/new` - Reset session, start new conversation context
- `/status` - View system status (session ID, database statistics, etc.)
- `/abort` - Abort current ongoing output
- `/mention` - Toggle whether @mention is required for the current channel (per-channel setting)
- `/split` - Toggle newline splitting mode for the current channel (per-channel setting)
- `/restart` - Restart service
- `/stop` - Stop service

**Discord Context Menus** (Right-click on message):
- **Download Attachments** - Right-click a message with attachments → Apps → Download Attachments

### 5.3 Image Stickers

Bot supports automatic image sticker sending to make conversations more lively and natural.

**Sticker Directory**: `stickers/`

**Send Format**: `<:filename.extension>`

**Examples**:
```
<:开心-森贝儿贵宾犬起飞.gif>
<:疑惑-小巫母鸡蹲.png>
<:无语.gif>
```

**How it works**:
- Claude Code includes `<:filename.extension>` format in replies
- Bot automatically detects and replaces with corresponding sticker image
- Stickers are split and sent in order based on their position in the text

**Sticker naming convention**:
- Format: `meaning-content.extension`
- Example: `开心-森贝儿贵宾犬起飞.gif` (happy-Sanbei pug taking off.gif)

### 5.4 File Operations

**Configure default download directory** (in `config.yaml`):
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/files/downloads"
```

**Discord Method 1: Send Attachments with Messages**

Simply attach files when @Bot, Bot will automatically download and pass attachment info:

```
[@YourBot with file attachments]
@YourBot Please help me analyze these files
```

**Discord Method 2: Download Attachments (Context Menu)**

If you sent a message with attachments but forgot to @Bot, you can right-click that message:

1. Right-click on the message with attachments
2. Select **Apps** → **Download Attachments**
3. Bot automatically downloads all attachments to configured directory

**WeChat Method: Send File Messages**

Simply send file messages to the Bot, it will automatically download them.

**Discord/WeChat Universal Method: Reference Attachment Metadata**

Reply to a message with files and `@Bot` (no need for `@` in WeChat), Bot will extract attachment metadata and send to Claude, but **will not download the file**:

```
[Reply to a message with an image]
@YourBot Please help me analyze this image
```

**Use cases:**
- Let Bot view a specific file
- File already exists locally, just need to reference it

**Difference from other methods:**
- Other methods: Download file to local
- Universal method: Only extract metadata, no download

## 🔌 MCP Server Integration

Claude Code can send files to Discord/WeChat and manage scheduled tasks via MCP protocol.

### HTTP Mode (Standalone, Recommended)

In HTTP mode, the MCP server runs as a background service and can be monitored via the Web interface.

**Config file location**: `%APPDATA%\Claude\claude_desktop_config.json`

**Add MCP server**:
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

**Web Interface Monitoring**:

MCP server starts automatically with `start.bat` / `restart.bat`. Visit Web Control Panel to monitor MCP Server status and logs in real-time.

### MCP Tools

**Discord File Transfer**:
1. **send_file_to_discord** - Send file to Discord (supports DM/channels)
2. **send_multiple_files_to_discord** - Batch send files to Discord (up to 10 files)

**WeChat File Transfer**:
1. **send_file_to_weixin** - Send file to WeChat (supports DM/groups)
2. **send_multiple_files_to_weixin** - Batch send files to WeChat (up to 9 files)

**Scheduled Tasks**:
1. **add_cron** - Add scheduled task (supports cron expressions)
2. **list_cron** - List all scheduled tasks
3. **delete_cron** - Delete scheduled task
4. **toggle_cron** - Enable/disable scheduled task
5. **get_cron_info** - Get scheduled task details
6. **update_cron** - Update scheduled task
7. **get_current_time** - Get current time (supports multiple timezones)


## ⚙️ Configuration Options

### Main config.yaml Settings

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"  # Discord Bot Token
  allowed_channels: []                # Allowed channels (empty = all)
  allowed_users: []                   # Allowed users (empty = all)
  mention_required: true              # Default for new channels (can be toggled per-channel via /mention)

claude:
  executable: "claude"                 # Claude Code CLI command
  timeout: 300                         # Timeout (seconds)
  max_attempts: 3                      # Max call attempts (including first)
  working_directory: ""               # Working directory (optional)
  max_concurrent_sessions: 5           # Max concurrent sessions (0 = unlimited)
  worker_idle_timeout: 300             # Worker idle timeout (seconds, 0 = never cleanup)

file_download:
  default_directory: "D:/AgentWorkspace/downloads"  # Default download directory

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                   # Poll interval (milliseconds)
  message_retention_hours: 24          # Message retention time
  send_interval: 1.5                   # Message send interval (seconds)

message_splitting:
  enabled: true                        # Enable message splitting by empty lines (Make replies more natural and personified)

auto_load:
  enabled: true                        # Auto-inject prompt for first message of a new session
  prompt_text: "Read and follow CLAUDE.md..."  # Prompt text to inject

auto_trigger_before_new:
  enabled: false                       # Auto-send prompt before /new (lets Claude do final tasks in old session, e.g. save memories)
  prompt_text: "Session is about to reset, please save important memories."  # Preset user message to send automatically

auto_trigger_after_new:
  enabled: true                        # Auto-trigger conversation after /new command
  prompt_text: "Execute session startup."  # Preset user message to send automatically

typing_indicator:
  max_retries: 3                       # Max consecutive retries (network fluctuations)
  retry_delay: 3                       # Retry wait time (seconds)

timeout:
  pending: 30                          # PENDING state timeout (seconds)

tool_use_notification:
  enabled: true                        # Enable tool use notification (forward as Embed cards)

# WeChat Bot configuration (optional)
weixin:
  enabled: false                       # Enable WeChat Bot
  accounts_file: "./config/weixin_accounts.json"  # WeChat account storage file path
```

### WeChat Bot Configuration (Optional)

To use WeChat support, follow these steps:

#### 1. WeChat QR Code Login

**Method 1: Interactive Login (for first-time account setup)**

```bash
python scripts/login_weixin.py
```

Scan the QR code in the terminal to log in to WeChat. You can run this command multiple times to add multiple WeChat accounts.

**Method 2: Quick Re-login (when existing account token expires)**

```bash
# Step 1: Get QR code link
python scripts/get_weixin_qrcode.py

# Step 2: After scanning the QR code, poll login status and update config
python scripts/poll_weixin_login.py <qrcode_id> <username>
# Example: python scripts/poll_weixin_login.py 51c0f8844acb0552fe6a3741545802fb 猪猪大王
```

Specifying `username` will automatically update the bot_id and bot_token for that user in `weixin_accounts.json`, preserving the existing username, user_id and other settings. Without `username`, it only prints the login result.

#### 2. Enable WeChat Bot

Enable WeChat Bot in `config.yaml`:

```yaml
weixin:
  enabled: true
```

#### 3. Start Service

WeChat Bot will automatically start together with Discord Bot, both running independently without affecting each other.

## 🔧 Troubleshooting

### Web Panel Not Accessible

1. Check if Web Server is running: Visit Web Control Panel
2. Check service status: View component status in the left sidebar of Web Panel
3. Check port availability: Ensure the configured port is not occupied

### Bot Not Responding

1. Check if Discord Token is correct
2. Confirm Bot has sufficient permissions
3. Confirm Message Content Intent is enabled
4. Check real-time logs in Web Panel for troubleshooting

### Claude Code Not Responding

1. Test CLI: `claude -p "test"`
2. Check if logged in: `claude --version`
3. View Bridge logs in Web Panel
4. Check if working directory is configured correctly

### Download Timeout

- Fixed: Using polling to check status (every 2 seconds)
- Large files may take longer, please be patient
- If timeout persists, check if Bot process is running in Web Panel

## 📄 License

MIT License

## 🤝 Contributing

Issues and Pull Requests are welcome!
