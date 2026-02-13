# Discord Bridge MCP 服务器配置指南

## 简介

Discord Bridge MCP 服务器为 Claude Code CLI 提供了 Discord 文件和消息发送功能。通过 MCP (Model Context Protocol) 协议，Claude Code 可以直接调用 Discord API 发送文件和消息。

## 功能特性

- ✅ 发送文件到 Discord 用户私聊
- ✅ 发送文件到 Discord 频道
- ✅ 批量发送多个文件（最多 10 个）
- ✅ 发送文本消息（支持私聊/频道）
- ✅ 列出 Bot 可访问的频道和服务器
- ✅ 支持 Embed 精美卡片格式
- ✅ 完整的错误处理和状态反馈

## 已注册的 MCP 工具

### 1. send_file_to_discord
发送文件到 Discord（支持用户私聊或频道）

**参数：**
- `file_path` (必需): 文件路径
- `user_id` (可选): Discord 用户 ID
- `channel_id` (可选): Discord 频道 ID
- `use_embed` (可选): 是否使用 Embed 格式（默认 False）

**注意：** `user_id` 和 `channel_id` 必须指定其中一个

### 2. send_multiple_files_to_discord
批量发送多个文件到 Discord（最多 10 个）

**参数：**
- `file_paths` (必需): 文件路径列表（最多 10 个）
- `user_id` (可选): Discord 用户 ID
- `channel_id` (可选): Discord 频道 ID
- `use_embed` (可选): 是否使用 Embed 格式（默认 False）

### 3. list_discord_channels
列出 Bot 可访问的所有频道和服务器

**参数：** 无

## 配置步骤

### 方法一：使用 stdio 传输（推荐）

stdio 传输模式是 Claude Code 的默认传输方式，配置简单且稳定。

#### 1. 编辑 Claude Code 配置文件

Claude Code 的 MCP 配置文件位置：

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS/Linux:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

#### 2. 添加 MCP 服务器配置

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

**配置说明：**
- `discord-bridge`: MCP 服务器名称（可自定义）
- `command`: Python 解释器路径（如果已在 PATH 中可省略完整路径）
- `args`: 服务器脚本路径和参数
  - 第一个参数：MCP 服务器脚本的完整路径
  - `--transport stdio`: 使用 stdio 传输模式
- `env`: 环境变量（可选）
  - `PYTHONPATH`: Python 模块搜索路径

#### 3. 重启 Claude Code

配置完成后，完全关闭并重新启动 Claude Code 应用程序。

### 方法二：使用 HTTP 传输

HTTP 传输模式适合需要远程访问或调试的场景。

#### 1. 启动 MCP 服务器

在终端中运行：

```bash
cd D:\AgentWorkspace\discord-claude-bridge
python mcp_server\server.py --transport http --host 0.0.0.0 --port 3334
```

#### 2. 编辑 Claude Code 配置文件

```json
{
  "mcpServers": {
    "discord-bridge": {
      "url": "http://localhost:3334/mcp"
    }
  }
}
```

#### 3. 重启 Claude Code

## 验证 MCP 连接

启动 Claude Code 后，在对话中测试 MCP 工具：

```
你：请列出 Discord Bot 可访问的所有频道
```

Claude Code 应该会自动调用 `list_discord_channels` 工具并返回结果。

## 使用示例

### 发送文件到用户私聊

```
你：请将 D:\charts\sales.png 发送给用户 123456789
```

Claude Code 会自动调用：
```python
send_file_to_discord(
    file_path="D:\\charts\\sales.png",
    user_id="123456789"
)
```

### 发送文件到频道

```
你：请将报告发送到频道 987654321
```

Claude Code 会自动调用：
```python
send_file_to_discord(
    file_path="D:\\reports\\monthly.pdf",
    channel_id="987654321",
    message="月度销售报告"
)
```

### 批量发送文件

```
你：请将这些图片打包发送给用户：image1.png, image2.png, image3.png
```

Claude Code 会自动调用：
```python
send_multiple_files_to_discord(
    file_paths=["image1.png", "image2.png", "image3.png"],
    user_id="123456789",
    message="图片合集"
)
```

### 使用 Embed 格式

```
你：用精美格式发送这个图表到我的私聊
```

Claude Code 会自动调用：
```python
send_file_to_discord(
    file_path="chart.png",
    user_id="123456789",
    use_embed=True
)
```

## 获取 Discord ID

### 用户 ID

1. 在 Discord 中启用**开发者模式**
   - 设置 → 高级 → 开发者模式

2. 右键点击用户
   - 复制用户 ID

### 频道 ID

1. 在 Discord 中启用**开发者模式**

2. 右键点击频道
   - 复制频道 ID

## 故障排查

### MCP 服务器无法连接

**问题：** Claude Code 无法连接到 MCP 服务器

**解决方案：**

1. **检查 Python 环境**
   ```bash
   python --version
   pip list | findstr discord
   ```

2. **验证脚本路径**
   - 确保配置文件中的路径正确
   - 使用正斜杠 `/` 或双反斜杠 `\\`

3. **查看 MCP 日志**
   - Claude Code 日志位置：
     - Windows: `%APPDATA%\Claude\logs\`
     - macOS: `~/Library/Logs/Claude/`

4. **手动测试 MCP 服务器**
   ```bash
   cd D:\AgentWorkspace\discord-claude-bridge
   python mcp_server\server.py --transport stdio
   ```

### Discord Bot 无法连接

**问题：** MCP 工具返回 "找不到用户" 或 "找不到频道" 错误

**解决方案：**

1. **验证 Bot Token**
   - 检查 `config.yaml` 中的 Token 是否正确

2. **检查 Bot 权限**
   - 确认 Bot 有访问目标频道/用户的权限

3. **启用必要的 Intents**
   - 在 Discord Developer Portal 启用：
     - Message Content Intent
     - Server Members Intent（可选）

### 文件发送失败

**问题：** 文件发送时返回错误

**解决方案：**

1. **检查文件路径**
   - 确保文件存在且路径正确
   - 使用绝对路径

2. **检查文件大小**
   - 普通服务器限制：25MB
   - Nitro 用户限制：500MB

3. **检查文件格式**
   - Discord 支持大多数常见格式
   - 某些特殊格式可能被阻止

## 安全建议

### 保护敏感信息

- ✅ 不要将 `config.yaml` 提交到版本控制
- ✅ 使用环境变量存储 Discord Token
- ✅ 限制 MCP 服务器的访问权限

### 权限控制

在 `config.yaml` 中配置允许的用户和频道：

```yaml
discord:
  allowed_channels: [123456789012345678]  # 只允许特定频道
  allowed_users: [987654321098765432]     # 只允许特定用户
```

### 文件访问控制

MCP 服务器可以访问系统中的任何文件，建议：

- ✅ 限制 Claude Code 的工作目录
- ✅ 使用虚拟环境隔离 Python 依赖
- ✅ 定期审查 MCP 服务器的文件访问日志

## 高级配置

### 自定义环境变量

在 MCP 配置中添加环境变量：

```json
{
  "mcpServers": {
    "discord-bridge": {
      "command": "python",
      "args": ["D:\\...\\server.py"],
      "env": {
        "PYTHONPATH": "D:\\...\\discord-claude-bridge",
        "DISCORD_TOKEN": "your_token_here",
        "LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

### 使用虚拟环境

```json
{
  "mcpServers": {
    "discord-bridge": {
      "command": "D:\\...\\venv\\Scripts\\python.exe",
      "args": ["D:\\...\\server.py"],
      "env": {
        "PYTHONPATH": "D:\\...\\discord-claude-bridge"
      }
    }
  }
}
```

### 调试模式

手动启动服务器时启用详细日志：

```bash
python mcp_server\server.py --transport stdio
```

查看服务器输出以诊断问题。

## 技术架构

```
Claude Code CLI
    ↓ (MCP Protocol - stdio/HTTP)
Discord Bridge MCP Server
    ↓ (Discord API)
Discord Bot
    ↓
Discord Servers/Channels/Users
```

## 相关资源

- [MCP 协议规范](https://modelcontextprotocol.io/)
- [Discord.py 文档](https://discordpy.readthedocs.io/)
- [Claude Code 文档](https://claude.ai/code)
- [Discord Developer Portal](https://discord.com/developers/applications)

## 更新日志

### v1.1.0 (2025-02-09)
- ✨ 添加频道发送支持
- ✨ 添加批量文件发送功能
- ✨ 所有工具支持发送到频道和私聊
- 📝 完善文档和错误提示

### v1.0.0 (初始版本)
- ✅ 基础文件发送功能
- ✅ 文本消息发送
- ✅ 频道列表查询

## 许可证

MIT License
