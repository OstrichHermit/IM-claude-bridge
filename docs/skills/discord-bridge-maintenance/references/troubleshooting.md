# Discord Claude Bridge - 故障排查指南

## 常见问题与解决方案

### 1. Bot 无响应

#### 症状
- Discord Bot 在线，但不回复消息
- 消息发送后没有任何反应

#### 诊断步骤

**步骤 1：检查服务是否运行**

```bash
# Windows PowerShell
Get-Process | Where-Object {$_.ProcessName -like "*python*"}

# 或查看是否有 discord_bot.py 和 claude_bridge.py 进程
```

**步骤 2：检查数据库**

```bash
cd discord-claude-bridge/shared
sqlite3 messages.db "SELECT id, status, direction, created_at FROM messages ORDER BY created_at DESC LIMIT 10;"
```

查看是否有：
- `status=pending` 的消息（未被处理）
- `status=processing` 的消息（处理中但未完成）

**步骤 3：检查日志**

查看控制台输出中的错误信息。

#### 解决方案

**问题 A：Discord Bot 未运行**

启动 Discord Bot：
```bash
cd discord-claude-bridge/bot
python discord_bot.py
```

**问题 B：Claude Bridge 未运行**

启动 Claude Bridge：
```bash
cd discord-claude-bridge/bridge
python claude_bridge.py
```

**问题 C：消息卡在 processing 状态**

原因：Claude Bridge 调用 Claude CLI 失败或超时。

解决方案：
1. 检查 Claude CLI 是否正常工作：
   ```bash
   claude -p "hello"
   ```

2. 增加超时时间（`config.yaml`）：
   ```yaml
   claude:
     timeout: 600  # 增加到 10 分钟
   ```

3. 查看是否有 Python 错误堆栈

**问题 D：数据库锁定**

原因：多个进程同时访问数据库。

解决方案：
1. 停止所有 Bot 和 Bridge 进程
2. 删除数据库锁文件（如果有）：
   ```bash
   rm shared/messages.db-shm
   rm shared/messages.db-wal
   ```
3. 重新启动服务

---

### 2. 权限错误

#### 症状
- Bot 返回权限不足的错误消息
- 特定用户或频道无法使用 Bot

#### 诊断步骤

检查 `config.yaml` 中的权限配置：
```yaml
discord:
  allowed_users: [1234567890, 9876543210]
  allowed_channels: [1111111111, 2222222222]
```

#### 解决方案

**问题 A：用户 ID 不在允许列表中**

添加用户 ID 到 `allowed_users`，或设置为空列表允许所有用户。

**问题 B：频道 ID 不在允许列表中**

添加频道 ID 到 `allowed_channels`，或设置为空列表允许所有频道。

**问题 C：获取正确的 ID**

1. 在 Discord 中开启开发者模式：
   - 设置 → 高级 → 开启开发者模式

2. 右键点击用户/频道 → 复制 ID

---

### 3. Claude CLI 错误

#### 症状
- Claude Bridge 进程崩溃
- 数据库中出现 `status=failed` 的消息
- 错误消息中包含 "claude" 相关错误

#### 常见错误类型

**错误 A：`FileNotFoundError: claude not found`**

原因：Claude CLI 不在系统 PATH 中。

解决方案：
1. 安装 Claude Code CLI
2. 或在 `config.yaml` 中指定完整路径：
   ```yaml
   claude:
     executable: "/absolute/path/to/claude"
   ```

**错误 B：`TimeoutError`**

原因：Claude CLI 响应时间超过配置的超时时间。

解决方案：
1. 增加超时时间：
   ```yaml
   claude:
     timeout: 600  # 10 分钟
   ```

2. 检查查询是否过于复杂

**错误 C：`AuthenticationError`**

原因：Claude CLI 未登录或认证过期。

解决方案：
```bash
claude auth login
```

---

### 4. 私聊问题

#### 症状
- 私聊 Bot 没有响应
- 错误消息："找不到用户"

#### 解决方案

**问题 A：使用 `get_user()` 导致用户找不到**

已修复：使用 `await self.fetch_user(user_id)` 从 Discord API 获取用户。

如果仍然出现此问题，检查：
1. `bot/discord_bot.py:227-236` 的代码
2. 确保使用 `fetch_user()` 而不是 `get_user()`

**问题 B：用户不在任何共享服务器**

`get_user()` 只能从缓存获取用户，而 `fetch_user()` 可以从 API 获取。

---

### 5. 配置文件错误

#### 症状
- 启动时报错：`FileNotFoundError: 配置文件不存在`
- 或：`yaml.YAMLError` 相关错误

#### 解决方案

**问题 A：配置文件不存在**

```bash
cd discord-claude-bridge/config
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入正确的配置
```

**问题 B：YAML 格式错误**

检查：
1. 缩进是否正确（使用空格，不要用 Tab）
2. 列表格式是否正确
3. 字符串引号是否配对

使用测试脚本验证：
```bash
python test_setup.py
```

---

### 6. 数据库问题

#### 症状
- `sqlite3.OperationalError`
- `database is locked`

#### 解决方案

**问题 A：数据库文件不存在**

自动创建：首次运行时会自动创建数据库。

**问题 B：数据库锁定**

原因：多个进程同时写入数据库。

解决方案：
1. 停止所有 Bot 和 Bridge 进程
2. 检查是否有其他进程占用数据库
3. 重新启动服务

**问题 C：数据库损坏**

修复或重建数据库：
```bash
cd discord-claude-bridge/shared
rm messages.db
# 重新启动服务，会自动创建新数据库
```

---

### 7. 消息重复或丢失

#### 症状
- Bot 多次发送相同的响应
- 消息发送后没有收到响应

#### 解决方案

**问题 A：消息重复**

原因：Discord Bot 或 Claude Bridge 多次重启。

解决方案：
1. 确保只有一个 Discord Bot 进程在运行
2. 确保只有一个 Claude Bridge 进程在运行

**问题 B：消息丢失**

原因：轮询间隔太长或服务未运行。

解决方案：
1. 检查两个服务是否都在运行
2. 减少轮询间隔：
   ```yaml
   queue:
     poll_interval: 300  # 减少到 300 毫秒
   ```

---

### 8. 长消息截断

#### 症状
- 长响应被截断
- Discord 显示不完整

#### 解决方案

已实现自动分割：
- 短消息（<4000字符）：放在 Embed 描述中
- 长消息：分割为 Embed 字段（最多 25 个，每个 1000 字符）
- 超长消息：发送多个 Embed

如果仍有问题，检查 `bot/discord_bot.py` 中的分割逻辑。

---

## 诊断工具

### 测试配置

```bash
cd discord-claude-bridge
python test_setup.py
```

输出：
- ✅ 配置文件存在
- ✅ YAML 格式正确
- ✅ Discord Token 已设置
- ✅ 数据库连接成功
- ✅ Claude CLI 可用

### 查看数据库状态

```bash
cd discord-claude-bridge/shared
sqlite3 messages.db

# 查看最近的消息
SELECT id, status, direction, created_at FROM messages ORDER BY created_at DESC LIMIT 10;

# 查看待处理的消息
SELECT COUNT(*) FROM messages WHERE status = 'pending';

# 查看失败的消息
SELECT id, error, created_at FROM messages WHERE status = 'failed';
```

### 清理数据库

使用维护脚本（见 `scripts/clean_queue.py`）：
```bash
python scripts/clean_queue.py --retention 24  # 清理 24 小时前的消息
```

---

## 获取帮助

如果以上解决方案都无法解决问题：

1. **收集信息**：
   - 错误消息的完整堆栈跟踪
   - `config.yaml` 配置（隐藏敏感信息）
   - 数据库状态（`status`, `direction`, `error` 字段）

2. **检查日志**：
   - Discord Bot 控制台输出
   - Claude Bridge 控制台输出

3. **验证环境**：
   - Python 版本（`python --version`）
   - discord.py 版本（`pip show discord.py`）
   - Claude CLI 版本（`claude --version`）
