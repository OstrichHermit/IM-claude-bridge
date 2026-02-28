# Discord Claude Bridge

A two-way communication system that bridges Discord messages to your local Claude Code CLI.

将 Discord 消息桥接到本地 Claude Code CLI 的双向通信系统。

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ Features

**Message Interaction**
- ✅ @Bot to call local Claude Code CLI
- ✅ Continuous conversation support (session management)
- ✅ Real-time status feedback (received → processing → response)
- ✅ Message tracking system (avoid duplicate processing)
- ✅ Response modes: Embed mode (default, card-style) + Direct reply mode (streaming)

**File Transfer**
- ✅ Upload files to workspace (`/upload` slash command, supports up to 25 files)
- ✅ Reference attachment metadata (extract attachment info and send to Claude)
- ✅ Download attachments from Discord to local
- ✅ Send files to Discord via MCP
- ✅ Batch file transfer support

**Service Management**
- ✅ Windows daemon process (auto monitor & restart)
- ✅ Discord slash commands (`/new`, `/status`, `/restart`, `/stop`, `/upload`)
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
    └── skills/                       # Maintenance Skill directory
        └── discord-bridge-maintenance/  # Maintenance Skill for this project
```

**Maintenance Skill Usage** (recommended installation):
```bash
# Copy maintenance Skill to Claude Code config directory
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/

# Copy scheduled task Skill (optional, for creating scheduled reminders)
cp -r docs/skills/scheduler-task ~/.claude/skills/
```

**discord-bridge-maintenance Skill Features**:
- 🔧 View system architecture and configuration
- 📊 Monitor message queue and download status
- 🐛 Quick troubleshooting (Bot not responding, download timeout, etc.)
- 📝 View database records (messages, download requests)
- 🔄 View pending task list

**scheduler-task Skill Features** (scheduled reminders):
- ⏰ Create and manage Windows scheduled tasks
- 📝 Write async-executing batch scripts (avoid task blocking)
- 🎯 Configure Discord Bridge command parameters (support DM & channels)
- 🔧 Properly handle Chinese character encoding issues
- 📅 Implement scheduled reminders, calendar notifications, task reports, and other automation

**Typical Use Cases**:
- Daily scheduled reminders (health reminders: brushing, resting, hydration, etc.)
- Scheduled reports (hourly/daily status reports)
- Calendar notifications (meeting reminders, deadline alerts)
- Automated workflows (execute scripts on schedule and send result notifications)

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

#### 5.4 Response Modes

The system supports two response modes, configurable via `config.yaml`:

**Embed Mode** (default):
- Sends confirmation message ("⏳ Message received")
- Displays response using Discord Embed cards
- Suitable for long replies, formatted content
- Single message (auto-split if too long)

**Direct Reply Mode** (requires enabling):
- No confirmation message sent
- Claude's response sent directly (streaming output)
- Each block sent as independent message
- Shows typing indicator
- Suitable for real-time conversations, quick responses

**Mode Comparison**:

| Feature | Embed Mode (Default) | Direct Reply Mode |
|---------|---------------------|-------------------|
| Confirmation message | ✅ Sent | ❌ Not sent |
| Response format | Embed card | Plain text message |
| Message count | 1 (may split) | Multiple (one per block) |
| Best for | Long replies | Real-time conversations |

**Configure Direct Reply Mode** (in `config.yaml`):
```yaml
direct_reply:
  enabled: false  # Enable direct reply mode (default: disabled)
  streaming:
    min_message_interval: 1.5  # Message send interval (seconds), avoid Discord rate limit
    stop_typing_after_first_block: false  # Stop typing after first message
    merge_short_blocks: true  # Merge short blocks
    short_block_max_length: 50  # Max length for short blocks (characters)
```

#### 5.2 Slash Commands

- `/new` - Reset session, start new conversation context
- `/status` - View system status (session ID, database statistics, etc.)
- `/restart` - Restart service
- `/stop` - Stop service
- `/upload` - Upload files to workspace (supports batch upload), and send files back to original channel/DM

**Upload Features**:
- ✅ Support all attachment types (images, documents, archives, etc.)
- ✅ Batch upload (multiple attachments in one message)
- ✅ Auto handle filename conflicts (auto rename)

**Configure default directory** (in `config.yaml`):
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/downloads"
```

#### 5.3 File Operations

Reply to a message with attachments and @Bot, Bot will extract attachment metadata and send to Claude, Claude can operate based on attachment information:

```
[Reply to a message with an image]
@YourBot Please help me analyze this image
```

**Features**:
- ✅ Automatically extract attachment metadata (filename, size, URL)
- ✅ Send to Claude as structured data
- ✅ Claude can perform operations based on attachment information

## 🔌 MCP Server Integration

Claude Code can send files to Discord via MCP protocol.

### Configuration

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

1. **Send file to Discord** - Support user DM and channels
2. **Batch send files** - Up to 10 files at once
3. **List channels** - View all channels Bot can access

For detailed configuration, see: [MCP_SETUP.md](MCP_SETUP.md)

## ⚙️ Configuration Options

### Main config.yaml Settings

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"  # Discord Bot Token
  command_prefix: "@"                  # Command prefix
  allowed_channels: []                # Allowed channels (empty = all)
  allowed_users: []                   # Allowed users (empty = all)

claude:
  executable: "claude"                 # Claude Code CLI command
  timeout: 300                         # Timeout (seconds)
  max_retries: 3                       # Max retry count
  working_directory: ""               # Working directory (optional)

file_download:
  default_directory: "D:/AgentWorkspace/downloads"  # Default download directory

queue:
  database_path: "./shared/messages.db"
  poll_interval: 500                   # Poll interval (milliseconds)
  message_retention_hours: 24          # Message retention time

direct_reply:
  enabled: false                       # Enable direct reply mode (default: disabled)
  streaming:
    min_message_interval: 1.5          # Message send interval (seconds), avoid Discord rate limit
    stop_typing_after_first_block: false  # Stop typing after first message
    merge_short_blocks: true           # Merge short blocks
    short_block_max_length: 50         # Max length for short blocks (characters)
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
