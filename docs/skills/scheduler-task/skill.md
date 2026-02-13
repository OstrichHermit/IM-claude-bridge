---
name: scheduler-task
description: 创建和管理 Windows 计划任务定时提醒，包括编写异步执行的 bat 脚本、配置 Discord Bridge 命令参数、避免任务阻塞等最佳实践。当用户需要创建定时任务、设置提醒、编写 bat 脚本触发 Python 脚本、或配置自动化工作流时使用此 Skill。
---

# 定时任务系统管理

## Overview

此 Skill 提供创建和管理 Windows 计划任务的完整工作流，包括异步 bat 脚本编写、参数配置和性能优化。

**核心能力：**
- ✅ 创建一次性/循环定时任务
- ✅ 编写异步执行的 bat 脚本（避免阻塞）
- ✅ 配置 Discord Bridge `trigger_scheduled_task.py` 参数
- ✅ 完美支持中文字符（UTF-8 配置文件）
- ✅ 所有参数统一管理（配置文件集中管理）

## 快速开始

### 场景 1：创建一次性任务

**用户请求示例：**
- "创建一个一分钟后执行的任务"
- "明天上午10点发送提醒"
- "5分钟后执行检查"

**操作步骤：**
1. 创建任务文件夹和配置文件
2. 创建 bat 脚本（只需指定配置文件路径）
3. 使用 `schtasks` 命令创建任务
4. 指定执行时间 `/st`
5. 设置 `/sc once` 参数

**示例：一分钟后执行**
```bash
schtasks /create /tn "任务名称" /tr "D:\path\to\script.bat" /st (Get-Date).AddMinutes(1).ToString('HH:mm') /sc once /f
```

### 场景 2：创建循环任务

**用户请求示例：**
- "每天上午9点发送报告"
- "每小时检查一次状态"
- "每周一备份数据"

**操作步骤：**
1. 创建任务文件夹和配置文件
2. 创建 bat 脚本
3. 使用 `schtasks` 命令创建任务
4. 设置 `/sc daily` / `/sc hourly` / `/sc weekly`
5. 指定具体时间参数

**示例：每天上午9点执行**
```bash
schtasks /create /tn "任务名称" /tr "D:\path\to\script.bat" /st 09:00 /sc daily /f
```

## 配置文件方式

### 配置文件格式

**文件位置：** `任务名\任务名_config.txt`

**标准格式（ini 风格）：**
```ini
# 橘子提醒配置文件
# 参数说明：
# - username: 用户名
# - content: 消息内容
# - user_id: Discord 用户 ID（私聊模式必填）
# - channel_id: Discord 频道 ID（频道模式必填）
# - tag: 消息标签（reminder 或 task）

username=
content=
user_id=
channel_id=
tag=
```

**支持的参数：**

| 参数 | 说明 | 必填 | 示例 |
|------|------|------|------|
| `username` | 用户名 | ✅ | `鸵鸟居士` |
| `content` | 消息内容 | ✅ | `该去吃橘子了！🍊` |
| `user_id` | Discord 用户 ID | * | `USER_DISCORD_ID` |
| `channel_id` | Discord 频道 ID | * | `1466858871720251425` |
| `tag` | 消息标签 | ✅ | `reminder` 或 `task` |

* 注：`user_id` 和 `channel_id` 必须二选一，不能同时为空或同时填写。

**参数优先级：**
- 必须使用配置文件参数
- 在频道中创建的任务，必须在频道中发送
- 在私聊中创建的任务，必须在私聊中发送

**编码要求：**
- 配置文件必须使用 **UTF-8 编码**

## Bat 脚本模板

### 标准模板

**极简版本（所有参数在配置文件）：**
```batch
@echo off
REM ========================================
REM 任务名称：橘子提醒
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder_config.txt"

exit /b 0
```

**调试版本（带日志）：**
```batch
@echo off
REM ========================================
REM 任务名称：橘子提醒
REM 说明：添加日志输出
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder.log" 2>&1
pause
```

**关键点：**
- ⚠️ **使用绝对路径** - Windows 计划任务的工作目录可能不稳定
- ⚠️ **避免中文字符在文件名中** - 会导致命令截断
- ✅ **使用配置文件存储所有内容** - UTF-8 编码的 .txt 文件
- ✅ **每个任务独立文件夹** - 清晰管理 bat、txt、log 三个文件
- ✅ **日志重定向** - 方便调试和排查问题

### 文件夹结构规范

**标准目录结构：**
```
D:\AgentWorkspace\scheduled-tasks\
├── orange_reminder\
│   ├── orange_reminder.bat          # 调用脚本
│   ├── orange_reminder_config.txt    # 任务配置（UTF-8）
│   └── orange_reminder.log            # 执行日志
├── morning_reminder\
│   ├── morning_reminder.bat
│   ├── morning_reminder_config.txt
│   └── morning_reminder.log
└── bedtime_reminder\
    ├── bedtime_reminder.bat
    ├── bedtime_reminder_config.txt
    └── bedtime_reminder.log
```

**路径规范：**
- **任务文件夹**：`D:\AgentWorkspace\scheduled-tasks\任务名\`
  - 每个定时任务一个独立文件夹
  - 文件夹命名：英文（避免中文字符）
- **批处理文件**：`任务名\任务名.bat`
- **配置文件**：`任务名\任务名_config.txt`（UTF-8 编码）
- **日志文件**：`任务名\任务名.log`
- **Python 脚本位置**：使用绝对路径 `D:\AgentWorkspace\discord-claude-bridge\`

## Discord Bridge 参数配置

### trigger_scheduled_task.py 参数说明

**基本语法：**
```bash
# 使用配置文件（推荐，支持所有参数）
python trigger_scheduled_task.py --config-file "<配置文件路径>"

# 使用命令行参数（高级用法）
python trigger_scheduled_task.py --username "<用户名>" --user-id <用户ID> --tag reminder --content "消息内容"
```

**重要提示：**
- 推荐使用配置文件方式，完美支持中文
- 命令行参数可覆盖配置文件中的对应参数
- 私聊模式需要用户ID，频道模式需要频道ID

**命令行参数（可覆盖配置文件）：**
- `--config-file <路径>` - 消息配置文件（使用配置方式时必填）
- `--username <用户名>` - 用户名（可从配置文件读取）
- `--user-id <ID>` - Discord 用户 ID（可从配置文件读取）
- `--channel-id <ID>` - Discord 频道 ID（可从配置文件读取）
- `--content <文本>` - 消息内容（可从配置文件读取）
- `--tag <标签>` - 消息标签（可从配置文件读取）

**发送目标说明：**
- **私聊模式**（`user_id`）：消息发送到指定用户的 Discord 私聊
- **频道模式**（`channel_id`）：消息发送到指定 Discord 频道
- **必须二选一**：不能同时指定 user_id 和 channel_id

**如何获取频道 ID：**
1. 在 Discord 中开启开发者模式（设置 → 高级 → 开发者模式）
2. 右键点击频道，选择"复制 ID"
3. 频道 ID 格式类似：`1234567890123456789`

**标签选择指南：**
- **reminder** - 提醒类型（健康提醒、日程提醒、待办事项）
- **task** - 任务类型（执行任务、检查状态、发送报告）

### 配置文件示例

**私聊提醒示例：**
```ini
# 早上刷牙提醒
username=鸵鸟居士
content=早上好！该去刷牙了！🪥
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

**频道通知示例：**
```ini
# 每小时状态报告到频道
username=系统机器人
content=【系统状态报告】所有服务运行正常 ✅
user_id=
channel_id=1234567890123456789
tag=task
```

## Windows 计划任务工具参考

### 创建任务（schtasks）

**基本语法：**
```bash
schtasks /create /tn "任务名称" /tr "要执行的命令" /st "开始时间" /sc "调度类型" /f
```

**参数说明：**
- `/tn <名称>` - 任务名称（必填）
- `/tr <命令>` - 要执行的命令或批处理文件路径（必填）
- `/st <时间>` - 开始时间，格式 HH:mm（必填）
- `/sc <类型>` - 调度类型（必填）
  - `once` - 执行一次
  - `daily` - 每天
  - `weekly` - 每周
  - `monthly` - 每月
  - `hourly` - 每小时
- `/f` - 强制创建（覆盖已存在的同名任务）

**使用 PowerShell 计算动态时间：**
```powershell
# 一分钟后执行
schtasks /create /tn "任务名" /tr "D:\script.bat" /st (Get-Date).AddMinutes(1).ToString('HH:mm') /sc once /f

# 两小时后执行
schtasks /create /tn "任务名" /tr "D:\script.bat" /st (Get-Date).AddHours(2).ToString('HH:mm') /sc once /f

# 明天上午9点执行
schtasks /create /tn "任务名" /tr "D:\script.bat" /st "09:00" /sd (Get-Date).AddDays(1).ToString('yyyy/MM/dd') /sc once /f
```

### 常用调度示例

**一次性任务：**
```bash
# 1分钟后执行
schtasks /create /tn "临时任务" /tr "D:\script.bat" /st 10:30 /sc once /f

# 指定日期时间执行
schtasks /create /tn "定时任务" /tr "D:\script.bat" /st 09:00 /sd 2026/02/14 /sc once /f
```

**循环任务：**
```bash
# 每天上午9点执行
schtasks /create /tn "每日报告" /tr "D:\script.bat" /st 09:00 /sc daily /f

# 每小时执行一次
schtasks /create /tn "每小时检查" /tr "D:\script.bat" /sc hourly /f

# 每周一上午10点执行
schtasks /create /tn "周报" /tr "D:\script.bat" /st 10:00 /sc weekly /d MON /f

# 每月1号执行
schtasks /create /tn "月度总结" /tr "D:\script.bat" /st 00:00 /sc monthly /d 1 /f
```

### 查询和管理任务

**查询任务详情：**
```bash
# 列出所有任务
schtasks /query

# 查看特定任务的详细信息
schtasks /query /v /tn "任务名称"

# 使用 PowerShell 格式化输出
schtasks /query /v /tn "任务名称" | Format-List
```

**立即执行任务：**
```bash
schtasks /run /tn "任务名称"
```

**删除任务：**
```bash
schtasks /delete /tn "任务名称" /f
```

**禁用/启用任务：**
```bash
# 禁用任务
schtasks /change /tn "任务名称" /disable

# 启用任务
schtasks /change /tn "任务名称" /enable
```

## 调试和故障排查

### 1. 查看执行日志

**检查日志文件：**
```bash
# 如果脚本配置了日志重定向
type D:\AgentWorkspace\scheduled-tasks\task_name.log
```

**查看任务执行历史：**
```bash
# 查看上次运行时间和结果
schtasks /query /v /tn "任务名称" | findstr "Last Run Time Last Result"
```

### 2. 常见问题排查

**问题 1：任务显示已执行，但无效果**
- 检查批处理脚本是否可执行
- 查看日志文件是否有错误信息
- 确认路径是否使用绝对路径
- 验证配置文件格式是否正确（key=value）

**问题 2：配置文件参数不生效**
- 检查配置文件编码是否为 UTF-8
- 确认参数格式：`key=value`（等号两边无空格）
- 验证必填参数是否都提供：username、content、tag
- 验证 user_id 或 channel_id 二选一

**问题 3：计划任务创建失败**
- 检查路径是否包含特殊字符
- 确认批处理文件存在
- 使用 `/f` 参数强制覆盖
- 验证时间格式是否正确

**问题 4：Python 脚本找不到**
- 确认 Python 安装路径正确
- 使用完整的 Python 可执行文件路径
- 验证 `trigger_scheduled_task.py` 脚本路径存在

### 3. 测试流程

**推荐测试步骤：**
1. 先手动执行批处理脚本，确认功能正常
2. 查看配置文件是否被正确读取
3. 创建 1 分钟后执行的一次性任务测试
4. 检查日志输出，确认无误
5. 创建正式的生产任务
6. 验证任务执行结果

## 完整示例

### 示例 1：橘子提醒（私聊）

**1. 创建任务文件夹：**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\orange_reminder"
```

**2. 创建配置文件（`orange_reminder_config.txt`）：**
```ini
# 橘子提醒配置文件
username=鸵鸟居士
content=鸵鸟居士，该去吃橘子了！🍊
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

**3. 创建批处理文件（`orange_reminder.bat`）：**
```batch
@echo off
python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder.log" 2>&1
pause
```

**4. 测试执行：**
```bash
# 手动运行 bat 文件测试
"D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder.bat"

# 查看日志
type "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder.log"
```

**5. 创建定时任务（每天下午2点）：**
```bash
schtasks /create /tn "每日橘子提醒" /tr "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder.bat" /st 14:00 /sc daily /f
```

### 示例 2：每小时状态报告（频道）

**1. 创建任务文件夹：**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\hourly_report"
```

**2. 创建配置文件（`hourly_report_config.txt`）：**
```ini
# 每小时状态报告配置
username=系统机器人
content=【系统状态报告】所有服务运行正常 ✅
user_id=
channel_id=1234567890123456789
tag=task
```

**3. 创建批处理文件（`hourly_report.bat`）：**
```batch
@echo off
python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.log" 2>&1
pause
```

**4. 创建定时任务（每小时执行）：**
```bash
schtasks /create /tn "每小时状态报告" /tr "D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.bat" /sc hourly /f
```

### 示例 3：明天上午9点早安提醒

**1. 创建任务文件夹：**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\morning_reminder"
```

**2. 创建配置文件（`morning_reminder_config.txt`）：**
```ini
# 早安提醒配置
username=鸵鸟居士
content=早上好！美好的一天开始了！☀️
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

**3. 创建批处理文件（`morning_reminder.bat`）：**
```batch
@echo off
python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.log" 2>&1
pause
```

**4. 创建定时任务（明天上午9点）：**
```powershell
schtasks /create /tn "早安提醒" /tr "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.bat" /st 09:00 /sd (Get-Date).AddDays(1).ToString('yyyy/MM/dd') /sc once /f
```

## 最佳实践总结

1. ✅ **每个任务独立文件夹** - 清晰管理 bat、txt、log 文件
2. ✅ **使用绝对路径** - 避免工作目录问题
3. ✅ **使用配置文件方式** - 完美支持中文和 emoji
4. ✅ **所有参数集中管理** - 配置文件统一管理 username、content、user_id、channel_id、tag
5. ✅ **添加日志输出** - 方便调试和排查
6. ✅ **一次性任务测试** - 先验证再创建正式任务
7. ✅ **使用 PowerShell 计算时间** - 动态时间更灵活
8. ✅ **查看执行结果** - 确认任务成功执行
9. ✅ **及时删除无用任务** - 保持系统整洁
10. ✅ **正确使用 --tag 参数** - 根据事项性质选择 task 或 reminder 标签：
   - 提醒类事项（健康提醒、日程提醒）使用 `tag=reminder`
   - 任务类事项（执行脚本、发送报告）使用 `tag=task`
