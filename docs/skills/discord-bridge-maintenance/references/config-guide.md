# Discord Claude Bridge - 配置指南

## 配置文件位置

```
discord-claude-bridge/config/config.yaml
```

## 完整配置示例

```yaml
# Discord 配置
discord:
  # Discord Bot Token（必需）
  token: "YOUR_DISCORD_BOT_TOKEN_HERE"

  # 命令前缀（可选，默认 '@'）
  # 只有以此前缀开头的消息才会被处理
  command_prefix: "@"

  # 允许的频道 ID 列表（可选）
  # 留空表示允许所有频道
  allowed_channels: []

  # 允许的用户 ID 列表（可选）
  # 留空表示允许所有用户
  allowed_users: []

# Claude Code 配置
claude:
  # Claude CLI 可执行文件路径（可选，默认 'claude'）
  executable: "claude"

  # 超时时间（秒，默认 300）
  # Claude CLI 调用的最大等待时间
  timeout: 300

  # 最大重试次数（默认 3）
  # 调用失败时的重试次数
  max_retries: 3

  # 会话模式（默认 'none'）
  # - none: 每次都是新会话
  # - channel: 每个频道独立会话
  # - user: 每个用户独立会话
  # - global: 全局共享会话
  session_mode: "none"

  # 工作目录（可选）
  # 留空使用项目根目录
  # Claude CLI 将在此目录执行
  working_directory: ""

# 消息队列配置
queue:
  # 数据库路径（可选，默认 './shared/messages.db'）
  database_path: "./shared/messages.db"

  # 轮询间隔（毫秒，默认 500）
  # Discord Bot 和 Claude Bridge 检查队列的频率
  poll_interval: 500

  # 消息保留时间（小时，默认 24）
  # 已完成消息的保留时间，0 表示永久保留
  message_retention_hours: 24
```

## 配置项详解

### Discord 配置

#### token（必需）
Discord Bot 的 Token。

**获取方式**：
1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建应用或选择现有应用
3. 进入 "Bot" 页面
4. 点击 "Reset Token" 或复制现有 Token
5. 将 Token 粘贴到配置文件

**注意事项**：
- Token 是敏感信息，不要泄露
- 如果 Token 泄露，立即在 Discord Developer Portal 重置

#### command_prefix（可选）
命令前缀，只有以此前缀开头的消息才会被处理。

**示例**：
```yaml
command_prefix: "@"  # 必须提及 Bot
command_prefix: "!"  # 以 ! 开头
command_prefix: ""   # 所有消息都会被处理
```

**默认值**：`@`（需要 @提及 Bot）

#### allowed_channels（可选）
允许的频道 ID 列表。

**获取频道 ID**：
1. 在 Discord 中开启开发者模式
   - 设置 → 高级 → 开启开发者模式
2. 右键点击频道 → 复制频道 ID

**示例**：
```yaml
# 只允许特定频道
allowed_channels:
  - 123456789012345678
  - 987654321098765432

# 允许所有频道
allowed_channels: []
```

**注意事项**：
- 私聊消息不受此限制
- 空列表表示允许所有频道

#### allowed_users（可选）
允许的用户 ID 列表。

**获取用户 ID**：
1. 在 Discord 中开启开发者模式
2. 右键点击用户 → 复制用户 ID

**示例**：
```yaml
# 只允许特定用户
allowed_users:
  - 123456789012345678
  - 987654321098765432

# 允许所有用户
allowed_users: []
```

**注意事项**：
- 空列表表示允许所有用户
- 适用于私有 Bot 或测试环境

### Claude Code 配置

#### executable（可选）
Claude CLI 可执行文件的路径。

**示例**：
```yaml
# 系统 PATH 中的 claude
executable: "claude"

# 绝对路径
executable: "/usr/local/bin/claude"

# Windows 路径
executable: "C:\\Program Files\\Claude\\claude.exe"
```

**默认值**：`claude`

**验证安装**：
```bash
# 检查 claude 是否在 PATH 中
claude --version

# 检查特定路径
/path/to/claude --version
```

#### timeout（可选）
Claude CLI 调用的超时时间（秒）。

**示例**：
```yaml
# 5 分钟超时
timeout: 300

# 10 分钟超时
timeout: 600
```

**默认值**：`300`（5 分钟）

**注意事项**：
- 复杂查询可能需要更长时间
- 超时后会重试（根据 `max_retries`）

#### max_retries（可选）
调用失败时的最大重试次数。

**示例**：
```yaml
# 最多重试 3 次
max_retries: 3

# 不重试
max_retries: 0
```

**默认值**：`3`

#### session_mode（可选）
会话模式，决定如何保持 Claude 上下文。

**模式说明**：

| 模式 | 说明 | 工作目录 | 适用场景 |
|------|------|----------|----------|
| `none` | 每次都是新会话 | 临时目录 | 测试、无状态查询 |
| `channel` | 每个频道独立会话 | `sessions/channel_{id}/` | 多频道独立对话 |
| `user` | 每个用户独立会话 | `sessions/user_{id}/` | 个性化对话 |
| `global` | 全局共享会话 | 项目根目录 | 单一对话上下文 |

**示例**：
```yaml
# 每次新会话（无状态）
session_mode: "none"

# 每个频道独立
session_mode: "channel"

# 每个用户独立
session_mode: "user"

# 全局共享
session_mode: "global"
```

**默认值**：`none`

**注意事项**：
- `global` 模式下所有用户共享对话历史
- `channel` 和 `user` 模式会在 `sessions/` 目录创建子目录

#### working_directory（可选）
Claude CLI 的工作目录。

**示例**：
```yaml
# 使用项目根目录
working_directory: ""

# 相对路径
working_directory: "./workspace"

# 绝对路径
working_directory: "D:\\Projects\\MyProject"
```

**默认值**：空字符串（使用项目根目录）

**用途**：
- Claude 可以访问此目录中的文件
- 影响会话文件的存储位置（当 `session_mode != "none"`）

### 消息队列配置

#### database_path（可选）
消息队列数据库文件的路径。

**示例**：
```yaml
# 相对路径
database_path: "./shared/messages.db"

# 绝对路径
database_path: "D:\\Data\\messages.db"
```

**默认值**：`./shared/messages.db`

**注意事项**：
- 确保程序有写入权限
- 建议使用相对路径

#### poll_interval（可选）
Discord Bot 和 Claude Bridge 检查队列的间隔（毫秒）。

**示例**：
```yaml
# 500 毫秒（默认）
poll_interval: 500

# 更快的响应（100 毫秒）
poll_interval: 100

# 减少 CPU 占用（2000 毫秒）
poll_interval: 2000
```

**默认值**：`500`（0.5 秒）

**注意事项**：
- 较小的值 = 更快的响应，但更高的 CPU 占用
- 较大的值 = 更低的 CPU 占用，但更慢的响应

#### message_retention_hours（可选）
已完成消息的保留时间（小时）。

**示例**：
```yaml
# 保留 24 小时
message_retention_hours: 24

# 保留 7 天
message_retention_hours: 168

# 永久保留
message_retention_hours: 0
```

**默认值**：`24`

**注意事项**：
- 设置为 `0` 表示永久保留
- 自动清理由 `MessageQueue.cleanup_old_messages()` 执行

## 配置验证

使用测试脚本验证配置：

```bash
cd discord-claude-bridge
python test_setup.py
```

该脚本会：
1. 检查配置文件是否存在
2. 验证 YAML 格式
3. 检查必需的配置项
4. 测试数据库连接
5. 验证 Claude CLI 是否可用

## 常见配置问题

### 1. Token 错误
```
ValueError: 请先在 config.yaml 中设置有效的 Discord Bot Token
```

**解决**：
- 检查 `config.yaml` 中的 `discord.token` 是否正确
- 确保 Token 没有多余的空格或引号

### 2. Claude CLI 未找到
```
FileNotFoundError: claude not found
```

**解决**：
- 安装 Claude Code CLI
- 或在 `claude.executable` 中指定完整路径

### 3. 数据库权限错误
```
PermissionError: [Errno 13] Permission denied: './shared/messages.db'
```

**解决**：
- 检查 `queue.database_path` 指定的目录是否存在
- 确保程序有写入权限

### 4. 权限配置无效
Bot 不响应消息，即使配置了 `allowed_users` 或 `allowed_channels`。

**解决**：
- 确认 ID 是数字，不是字符串
- 使用开发者模式获取正确的 ID
- 检查配置文件格式（YAML 缩进）

## 安全建议

1. **保护 Token**：
   - 不要将 `config.yaml` 提交到版本控制
   - 使用 `config.example.yaml` 作为模板
   - 在 `.gitignore` 中添加 `config/config.yaml`

2. **限制访问**：
   - 生产环境使用 `allowed_users` 限制用户
   - 使用 `allowed_channels` 限制频道
   - 定期审查允许的用户和频道列表

3. **隔离环境**：
   - 测试环境和生产环境使用不同的 Bot Token
   - 使用不同的数据库文件

4. **定期更新**：
   - 定期更换 Discord Bot Token
   - 更新依赖包（discord.py、PyYAML 等）
