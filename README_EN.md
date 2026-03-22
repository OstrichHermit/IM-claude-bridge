# Discord Claude Bridge

A two-way communication system that bridges Discord messages to your local Claude Code CLI.

将 Discord 消息桥接到本地 Claude Code CLI 的双向通信系统。

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ Features

**💬 Message Interaction**
- ✅ @Bot to call local Claude Code CLI
- ✅ Continuous conversation support (session management)
- ✅ Real-time status feedback (received → processing → response)
- ✅ Message tracking system (avoid duplicate processing)
- ✅ Unified message queue mechanism for all responses
- ✅ Tool use notification (forward tool calls as Embed cards)

**📁 File Transfer**
- ✅ Send attachments with messages (auto download and pass attachment info)
- ✅ Context menu to download attachments (right-click)
- ✅ Reference attachment metadata (extract attachment info and send to Claude)
- ✅ Download attachments from Discord to local
- ✅ Send files to Discord via MCP
- ✅ Batch file transfer support

**⏰ Scheduled Tasks**
- ✅ Create and manage scheduled tasks (supports cron expressions)
- ✅ Support for DM and channel message pushing
- ✅ Task enable/disable/update/delete
- ✅ Execution history query

**🎯 Service Management**
- ✅ Windows daemon process (auto monitor & restart)
- ✅ Discord slash commands (`/new`, `/status`, `/abort`, `/restart`, `/stop`)
- ✅ Context menus (right-click message to download attachments)
- ✅ Message queue system (SQLite persistence)

## 🚀 Quick Start

### 0. Recommended Workspace Structure

**Highly recommended** to place this project in the root directory of your Claude Code workspace for easier management.

**Example structure**:
```
D:/AgentWorkspace/                    # Workspace root
├── discord-claude-bridge/            # Discord bridge project (this repo)
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
git clone https://github.com/OstrichHermit/discord-claude-bridge.git
cd discord-claude-bridge

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
     - `Send Messages`
     - `Read Messages/View Channels`
     - `Embed Links`
     - `Attach Files`
     - `Add Reactions`
     - `Use Slash Commands`
3. Bot page → **Privileged Gateway Intents** → Enable **Message Content Intent**
4. Copy the generated URL, open in browser → Select server → Authorize

> 💡 **Tip**: The same Bot can be invited to multiple servers. Just visit the same URL again and select a different server.

### 4. Start Service

**One-click start** (recommended):
```bash
start.bat
```

> Manager daemon will automatically start and monitor all services (Bot + Bridge)


### 5. Usage

#### 5.1 Basic Chat

Just @Bot in Discord:

```
@YourBot Please help me analyze this code
```

Bot will:
1. Receive message and show "⏳ Message received"
2. Forward to local Claude Code for processing (show "🔄 Processing")
3. Send Claude's reply back to Discord (show "✅ Message #X response successful!")

#### 5.2 Slash Commands

- `/new` - Reset session, start new conversation context
- `/status` - View system status (session ID, database statistics, etc.)
- `/abort` - Abort current ongoing output
- `/restart` - Restart service
- `/stop` - Stop service

**Context Menus** (Right-click on message):
- **Download Attachments** - Right-click a message with attachments → Apps → Download Attachments

#### 5.3 File Operations

**Configure default download directory** (in `config.yaml`):
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/files/downloads"
```

**Method 1: Send Attachments with Messages**

Simply attach files when @Bot, Bot will automatically download and pass attachment info:

```
[@YourBot with file attachments]
@YourBot Please help me analyze these files
```

**Attachment Processing Features**:
- ✅ Support all attachment types (images, documents, archives, etc.)
- ✅ Auto download to configured directory
- ✅ Auto handle filename conflicts (auto rename)
- ✅ Extract attachment metadata and send to Claude

**Method 2: Download Attachments (Context Menu)**

If you sent a message with attachments but forgot to @Bot, you can right-click that message:

1. Right-click on the message with attachments
2. Select **Apps** → **Download Attachments**
3. Bot automatically downloads all attachments to configured directory

**Method 3: Reference Attachment Metadata**

Reply to a message with attachments and @Bot, Bot will extract attachment metadata (filename, size, URL) and send to Claude, but **will not download the file**:

```
[Reply to a message with an image]
@YourBot Please help me analyze the metadata of this image
```

**Use cases:**
- Only need attachment information (filename, size, URL)
- Don't need to download the file
- File already exists locally, just need to reference it

**Difference from Method 1:**
- Method 1: Downloads file to local
- Method 3: Only extracts metadata, no download

## 🔌 MCP Server Integration

Claude Code can send files to Discord via MCP protocol.

### MCP Server Configuration

**Config file location**: `%APPDATA%\Claude\claude_desktop_config.json`

**Add MCP server**:
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

### MCP Tools

**File Transfer**:
1. **Send file to Discord** - Support user DM and channels
2. **Batch send files** - Up to 10 files at once

**Scheduled Tasks**:
1. **add_cron** - Add scheduled task (supports cron expressions)
2. **list_cron** - List all scheduled tasks
3. **delete_cron** - Delete scheduled task
4. **toggle_cron** - Enable/disable scheduled task
5. **get_cron_info** - Get scheduled task details
6. **update_cron** - Update scheduled task
7. **get_current_time** - Get current time (supports multiple timezones)

**Usage Examples**:
```python
# Add a daily 9 AM scheduled task
add_cron(
    cron_expr="0 9 * * *",
    content="Send daily report",
    username="鸵鸟居士",
    user_id="USER_DISCORD_ID",
    tag="task",
    description="Daily Report"
)

# Add an hourly reminder
add_cron(
    cron_expr="0 * * * *",
    content="Time to hydrate!",
    username="鸵鸟居士",
    user_id="USER_DISCORD_ID",
    tag="reminder"
)
```

For detailed configuration, see: [MCP_SETUP.md](MCP_SETUP.md)

## ⚙️ Configuration Options

### Main config.yaml Settings

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"  # Discord Bot Token
  allowed_channels: []                # Allowed channels (empty = all)
  allowed_users: []                   # Allowed users (empty = all)

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
  enabled: true                        # Enable message splitting by empty lines

typing_indicator:
  max_retries: 3                       # Max consecutive retries (network fluctuations)
  retry_delay: 3                       # Retry wait time (seconds)

timeout:
  pending: 30                          # PENDING state timeout (seconds)

tool_use_notification:
  enabled: true                        # Enable tool use notification (forward as Embed cards)
```

## 🔧 Troubleshooting

### Bot Not Responding

1. Check if Discord Token is correct
2. Confirm Bot has sufficient permissions
3. Confirm Message Content Intent is enabled

### Claude Code Not Responding

1. Test CLI: `claude -p "test"`
2. Check if logged in: `claude --version`
3. Check error logs in bridge service window

### Download Timeout

- Fixed: Using polling to check status (every 2 seconds)
- Large files may take longer, please be patient
- If timeout persists, check if Bot process is running

## 📄 License

MIT License

## 🤝 Contributing

Issues and Pull Requests are welcome!
