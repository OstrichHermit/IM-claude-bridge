# 配置文件示例

## reminder 类型配置示例

### 早上起床提醒

**文件：** `morning_reminder_config.txt`

```ini
# 早安提醒配置
username=鸵鸟居士
content<<<MARKER_START
早上好！☀️ 美好的一天开始了，记得吃早餐，保持好心情！
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

### 喝水提醒

**文件：** `water_reminder_config.txt`

```ini
# 喝水提醒配置
username=鸵鸟居士
content<<<MARKER_START
鸵鸟居士，该喝水了！💧 保持健康，多喝温水～
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

### 吃橘子提醒

**文件：** `orange_reminder_config.txt`

```ini
# 橘子提醒配置文件
username=鸵鸟居士
content<<<MARKER_START
鸵鸟居士，该去吃橘子了！🍊
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

### 睡觉提醒

**文件：** `bedtime_reminder_config.txt`

```ini
# 睡觉提醒配置
username=鸵鸟居士
content<<<MARKER_START
夜深了，该休息了！🌙 早睡早起身体好～
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

---

## task 类型配置示例

### 发送 PDF 文件到私聊

**文件：** `send_pdf_config.txt`

```ini
# 发送PDF文件任务配置
username=鸵鸟居士
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 files 文件夹下面的 新闻汇总_2026-02-09.pdf 文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

### 发送消息到私聊

**文件：** `send_message_config.txt`

```ini
# 发送消息任务配置
username=鸵鸟居士
content<<<MARKER_START
使用 Discord MCP 工具的 send_message_to_discord 函数，发送消息"服务运行正常✅"到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

### 运行脚本

**文件：** `run_script_config.txt`

```ini
# 运行脚本任务配置
username=鸵鸟居士
content<<<MARKER_START
使用 Bash 工具运行 D:\AgentWorkspace\scripts\backup.py 脚本，参数是 --full-backup --output-dir D:\AgentWorkspace\backups
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

### 读取文件并发送内容

**文件：** `read_and_send_config.txt`

```ini
# 读取文件并发送内容任务配置
username=鸵鸟居士
content<<<MARKER_START
先使用 Read 工具读取 D:\AgentWorkspace\reports\daily_report.txt 文件的完整内容，然后使用 Discord MCP 工具的 send_message_to_discord 函数将文件内容发送到我的私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

### 数据备份并发送通知

**文件：** `backup_data_config.txt`

```ini
# 数据备份任务配置
username=鸵鸟居士
content<<<MARKER_START
先使用 Bash 工具运行 D:\AgentWorkspace\scripts\backup.py 脚本，参数是 --full-backup --output-dir D:\AgentWorkspace\backups，等待脚本执行完成后，使用 Discord MCP 工具的 send_message_to_discord 函数发送消息"数据备份完成✅"到我的私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

---

## 频道模式配置示例

### 发送状态报告到频道

**文件：** `channel_report_config.txt`

```ini
# 每小时状态报告到频道
username=系统机器人
content<<<MARKER_START
使用 Discord MCP 工具的 send_message_to_discord 函数，发送消息"【系统状态报告】所有服务运行正常 ✅"到频道，channel_id 是 1466858871720251425
<<<MARKER_END
user_id=
channel_id=1466858871720251425
tag=task
```

---

## 配置文件编码说明

### UTF-8 编码重要性

**问题：** Windows 记事本默认使用 ANSI 编码，导致中文和 emoji 乱码。

**解决方法：**
1. 使用 VS Code、Notepad++ 等编辑器
2. 保存时选择 UTF-8 编码
3. VS Code：右下角选择编码 → UTF-8
4. Notepad++：编码 → 转为 UTF-8 编码

### 验证编码

**使用 VS Code：**
- 打开文件后，查看右下角编码显示
- 应该显示 "UTF-8"

**使用记事本：**
- 另存为 → 编码选择 "UTF-8"
- 不要选择 "UTF-8 with BOM"

---

## 配置文件格式检查清单

### reminder 类型检查清单

```
□ username 是否填写？
□ content 是否是最终要发送的消息？
□ user_id 或 channel_id 是否填写（二选一）？
□ tag 是否填写为 reminder？
□ 文件是否使用 UTF-8 编码？
□ 等号两边是否有空格（应该没有）？
```

### task 类型检查清单

```
□ username 是否填写？
□ content 是否包含明确的操作指令？
   - 是否指定使用哪个工具（Discord MCP、Bash、Read、Write）？
   - 是否指定完整路径（files/xxx.pdf 而非 xxx.pdf）？
   - 是否包含所有必要参数（user_id、channel_id、文件路径）？
□ user_id 或 channel_id 是否填写（二选一）？
□ tag 是否填写为 task？
□ 文件是否使用 UTF-8 编码？
□ 等号两边是否有空格（应该没有）？
```

---

## 常见错误示例

### 错误 1：task 类型的 content 写成结果消息

❌ **错误写法：**
```ini
username=鸵鸟居士
content<<<MARKER_START
PDF 已送达！
<<<MARKER_END
user_id=USER_DISCORD_ID
tag=task
```

**问题：** content 应该是执行指令，不是执行结果。

✅ **正确写法：**
```ini
username=鸵鸟居士
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 files 文件夹下面的 新闻汇总_2026-02-09.pdf 文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
tag=task
```

### 错误 2：路径不完整

❌ **错误写法：**
```ini
content<<<MARKER_START
使用 Discord MCP 发送 PDF 文件给我
<<<MARKER_END
```

**问题：** 路径不完整，新会话的 Claude 无法找到文件。

✅ **正确写法：**
```ini
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 files 文件夹下面的 新闻汇总_2026-02-09.pdf 文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
```

### 错误 3：等号两边有空格

❌ **错误写法：**
```ini
username = 鸵鸟居士
content = 使用 Discord MCP 发送 PDF
user_id = USER_DISCORD_ID
tag = task
```

**问题：** 等号两边的空格会导致参数无法正确解析。

✅ **正确写法：**
```ini
username=鸵鸟居士
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 files 文件夹下面的 新闻汇总_2026-02-09.pdf 文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
user_id=USER_DISCORD_ID
tag=task
```

### 错误 4：user_id 和 channel_id 同时填写或同时为空

❌ **错误写法 1：同时填写**
```ini
user_id=USER_DISCORD_ID
channel_id=1466858871720251425
```

❌ **错误写法 2：同时为空**
```ini
user_id=
channel_id=
```

✅ **正确写法：二选一**
```ini
# 私聊模式
user_id=USER_DISCORD_ID
channel_id=

# 频道模式
user_id=
channel_id=1466858871720251425
```
