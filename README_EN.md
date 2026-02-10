# Discord Claude Bridge

A two-way communication system that bridges Discord messages to your local Claude Code CLI.

将 Discord 消息桥接到本地 Claude Code CLI 的双向通信系统。

[English](README_EN.md) | [简体中文](README.md)

---

## ✨ Features

- ✅ @Bot to call Claude Code (supports continuous conversation)
- ✅ Message tracking system (real-time status updates)
- ✅ Session management (`/new` reset, `/status` check, `/restart` reboot)
- ✅ File download feature (download attachments from Discord to local)
- ✅ MCP server (Claude Code can send files to Discord)

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
# Copy Skill to Claude Code config directory
cp -r docs/skills/discord-bridge-maintenance ~/.claude/skills/
```

**Skill Features**:
- 🔧 View system architecture and configuration
- 📊 Monitor message queue and download status
- 🐛 Quick troubleshooting (Bot not responding, download timeout, etc.)
- 📝 View database records (messages, download requests)
- 🔄 View pending task list

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
2. OAuth2 → URL Generator → Check `bot`, `messages.read`, `messages.write`
3. Bot page → **Privileged Gateway Intents** → Enable **Message Content Intent**
4. Use the generated URL to invite Bot to your server

### 4. Start Service

**Start service**:
```bash
start.bat
```

**Restart service**:
```bash
restart.bat
```

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
- `/restart` - Restart service

#### 5.3 File Download

Reply to a message with attachments, @Bot and specify directory:

```
# Use default directory (D:/AgentWorkspace/downloads)
@YourBot download

# Specify directory
@YourBot download to D:/myfiles

# Direct path
@YourBot D:/AgentWorkspace/files
```

**Download Features**:
- ✅ Support all attachment types (images, documents, archives, etc.)
- ✅ Batch download (multiple attachments in one message)
- ✅ Auto handle filename conflicts (auto rename)
- ✅ Real-time progress updates (every 30 seconds)

**Configure default directory** (in `config.yaml`):
```yaml
file_download:
  default_directory: "D:/AgentWorkspace/downloads"
```

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
