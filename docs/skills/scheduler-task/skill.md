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
- ✅ 正确处理中文字符编码问题

## 快速开始

### 场景 1：创建一次性任务

**用户请求示例：**
- "创建一个一分钟后执行的任务"
- "明天上午10点发送提醒"
- "5分钟后执行检查"

**操作步骤：**
1. 创建 bat 脚本（参考 [批处理脚本模板](#bat-脚本模板)）
2. 使用 `schtasks` 命令创建任务
3. 设置 `/sc once` 参数
4. 指定执行时间 `/st`

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
1. 创建 bat 脚本（确保异步执行）
2. 使用 `schtasks` 命令创建任务
3. 设置 `/sc daily` / `/sc hourly` / `/sc weekly`
4. 指定具体时间参数

**示例：每天上午9点执行**
```bash
schtasks /create /tn "任务名称" /tr "D:\path\to\script.bat" /st 09:00 /sc daily /f
```

## Bat 脚本模板

### ✅ 标准模板（Windows 计划任务专用）

**Discord Bridge 提醒任务标准格式：**
```batch
@echo off
REM 1. 创建任务文件夹：D:\AgentWorkspace\scheduled-tasks\任务名\
REM 2. 创建配置文件：任务名\任务名_config.txt（UTF-8 编码）写入中文内容
REM 3. 使用 --config-file 参数读取
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\任务名\任务名_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\任务名\任务名.log 2>&1
exit /b 0
```

**关键点：**
- ⚠️ **使用绝对路径** - Windows 计划任务的工作目录可能不稳定
- ⚠️ **避免中文字符在文件名中** - 会导致命令截断
- ✅ **使用配置文件存储中文内容** - UTF-8 编码的 .txt 文件
- ✅ **每个任务独立文件夹** - 清晰管理 bat、txt、log 三个文件
- ✅ **日志重定向** - 方便调试和排查问题

**通用模板（带注释）：**
```batch
@echo off
REM 任务文件夹：D:\AgentWorkspace\scheduled-tasks\任务名\
REM 1. 创建配置文件：任务名\任务名_config.txt（UTF-8 编码）
REM 2. 使用绝对路径调用 Python 脚本
REM 3. 使用 --config-file 参数读取配置文件
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\任务名\任务名_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\任务名\任务名.log 2>&1

REM 立即退出批处理文件
exit /b 0
```

**路径规范：**
- **任务文件夹**：`D:\AgentWorkspace\scheduled-tasks\任务名\`
  - 每个定时任务一个独立文件夹
  - 文件夹命名：英文（避免中文字符）
- **批处理文件**：`任务名\任务名.bat`
- **配置文件**：`任务名\任务名_config.txt`（UTF-8 编码）
- **日志文件**：`任务名\任务名.log`
- **Python 脚本位置**：使用绝对路径 `D:\AgentWorkspace\discord-claude-bridge\`

**示例结构：**
```
D:\AgentWorkspace\scheduled-tasks\
├── brush_reminder\
│   ├── brush_reminder.bat
│   ├── brush_reminder_config.txt
│   └── brush_reminder.log
├── morning_reminder\
│   ├── morning_reminder.bat
│   ├── morning_reminder_config.txt
│   └── morning_reminder.log
└── bedtime_reminder\
    ├── bedtime_reminder.bat
    ├── bedtime_reminder_config.txt
    └── bedtime_reminder.log
```

### ❌ 错误示例（会导致问题）

```batch
@echo off
cd /d "D:\AgentWorkspace\discord-claude-bridge"
start "" /min python trigger_scheduled_task.py "鸵鸟居士，该去刷牙了！🪥" --user-id USER_DISCORD_ID >nul 2>&1
exit /b 0
```

**问题：**
- ❌ 使用相对路径 - Windows 计划任务的工作目录不可控
- ❌ 使用 `start "" /min` - 可能导致路径解析错误
- ❌ 中文和 emoji 在消息中 - 可能被截断破坏
- ❌ 输出到 nul - 无法调试

### ⚠️ 中文字符编码问题（重要）

**问题现象：**
Windows 计划任务执行时，包含中文字符的命令行可能被破坏：
```
'_task.py' is not recognized as an internal or external command
'r_scheduled_task.py' is not recognized as an internal or external command
```

**❌ 失败的解决方案：**
- 使用 `chcp 65001` 设置 UTF-8 代码页 - 无效
- 使用环境变量传递中文 - 变量展开失败
- 使用 `set /p` 从文件读取到变量 - 仍然失败

**✅ 最终解决方案：配置文件方式**

经过多次测试验证，最可靠的方法是**使用配置文件存储中文内容**，让 Python 脚本直接读取 UTF-8 编码的文件。

**文件结构：**
```
D:\AgentWorkspace\scheduled-tasks\
├── brush_reminder\
│   ├── brush_reminder.bat          # 调用脚本
│   ├── brush_reminder_config.txt    # 中文消息内容（UTF-8）
│   └── brush_reminder.log            # 执行日志
```

**步骤 1：创建任务文件夹**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\任务名"
```

**步骤 2：创建配置文件（UTF-8 编码）**
```
D:\AgentWorkspace\scheduled-tasks\任务名\任务名_config.txt 内容：
鸵鸟居士，该去刷牙了！🪥
```

**步骤 3：创建批处理脚本**
```batch
@echo off
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\任务名\任务名_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\任务名\任务名.log 2>&1
exit /b 0
```

**步骤 3：Python 脚本支持（已实现）**

`trigger_scheduled_task.py` 已支持 `--config-file` 参数：
- **`--config-file <文件路径>`** - 从 UTF-8 文件读取消息内容（必须使用）
- **`content` 参数** - 直接传递消息内容（已废弃，不推荐）

**优势：**
- ✅ 无需 `chcp` 编码设置
- ✅ 无需环境变量传递
- ✅ Python 直接读取 UTF-8 文件
- ✅ 完美支持中文、emoji 和特殊字符
- ✅ 经过实测验证稳定可靠

## Discord Bridge 参数配置

### trigger_scheduled_task.py 参数

**基本语法：**
```bash
# 发送到私聊
python trigger_scheduled_task.py --config-file "<配置文件路径>" --user-id <用户ID>

# 发送到频道
python trigger_scheduled_task.py --config-file "<配置文件路径>" --channel-id <频道ID>
```

**常用参数：**
- `--config-file <路径>` - 消息配置文件（必填，UTF-8 编码）
- `--user-id <ID>` - 发送到指定用户私聊（与 --channel-id 二选一）
- `--channel-id <ID>` - 发送到指定频道（与 --user-id 二选一）
- `--message <文本>` - 附加文本消息（可选）
- `--direction TO_CLAUDE|TO_USER` - 消息方向（默认：TO_CLAUDE）

**发送目标说明：**
- **私聊模式**（`--user-id`）：消息发送到指定用户的 Discord 私聊
- **频道模式**（`--channel-id`）：消息发送到指定 Discord 频道
- **必须二选一**：不能同时指定 --user-id 和 --channel-id

**如何获取频道 ID：**
1. 在 Discord 中开启开发者模式（设置 → 高级 → 开发者模式）
2. 右键点击频道，选择"复制 ID"
3. 频道 ID 格式类似：`1234567890123456789`

**示例：**
```batch
REM ========== 发送到私聊 ==========

REM 发送简单消息到私聊
python trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\message\message_config.txt" --user-id USER_DISCORD_ID

REM 发送带说明的消息到私聊
python trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\message\message_config.txt" --user-id USER_DISCORD_ID --message "请查收"

REM ========== 发送到频道 ==========

REM 发送消息到频道
python trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\message\message_config.txt" --channel-id 1234567890123456789

REM 发送带附件说明的消息到频道
python trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\message\message_config.txt" --channel-id 1234567890123456789 --message "详情见附件"

REM ========== 批处理脚本示例 ==========

REM 私聊提醒任务（保存为 morning_reminder/morning_reminder.bat）
@echo off
REM 1. 创建任务文件夹：D:\AgentWorkspace\scheduled-tasks\morning_reminder\
REM 2. 创建配置文件：morning_reminder\morning_reminder_config.txt（UTF-8 编码）写入中文内容
REM 3. 使用 --config-file 参数读取
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.log 2>&1
exit /b 0

REM 频道通知任务（保存为 channel_notify/channel_notify.bat）
@echo off
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\channel_notify\channel_notify_config.txt" --channel-id 1234567890123456789 >> D:\AgentWorkspace\scheduled-tasks\channel_notify\channel_notify.log 2>&1
exit /b 0
```

**典型应用场景：**
- **私聊提醒**：刷牙提醒、休息提醒、日程提醒等个人通知
- **频道通知**：定时报告、系统状态、任务完成等群组广播
- **混合使用**：重要通知同时发送到个人和频道

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

**问题 2：中文字符被截断**
- 改用英文消息内容
- 检查日志文件确认截断情况
- 使用绝对路径避免相对路径解析

**问题 3：计划任务创建失败**
- 检查路径是否包含特殊字符
- 确认批处理文件存在
- 使用 `/f` 参数强制覆盖

### 3. 测试流程

**推荐测试步骤：**
1. 先手动执行批处理脚本，确认功能正常
2. 创建 1 分钟后执行的一次性任务测试
3. 检查日志输出，确认无误
4. 如果需要中文消息，测试通过后再使用
5. 创建正式的生产任务

## 完整示例

### 示例：每小时发送状态报告

**1. 创建任务文件夹和批处理文件：**
```bash
# 创建文件夹
mkdir "D:\AgentWorkspace\scheduled-tasks\hourly_report"
```

```batch
@echo off
REM 保存为：D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.bat
REM 创建配置文件：hourly_report\hourly_report_config.txt（UTF-8 编码）
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.log 2>&1
exit /b 0
```

**2. 创建定时任务（每小时执行）：**
```bash
schtasks /create /tn "每小时状态报告" /tr "D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.bat" /sc hourly /f
```

**3. 验证：**
```bash
# 查看任务是否创建成功
schtasks /query /tn "每小时状态报告"

# 立即执行测试
schtasks /run /tn "每小时状态报告"

# 查看日志
type D:\AgentWorkspace\scheduled-tasks\hourly_report\hourly_report.log
```

### 示例：明天上午9点发送提醒

**1. 创建任务文件夹和批处理文件：**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\morning_reminder"
```

```batch
@echo off
REM 保存为：D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.bat
REM 创建配置文件：morning_reminder\morning_reminder_config.txt（UTF-8 编码）
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.log 2>&1
exit /b 0
```

**2. 创建定时任务（明天上午9点）：**
```powershell
schtasks /create /tn "早安提醒" /tr "D:\AgentWorkspace\scheduled-tasks\morning_reminder\morning_reminder.bat" /st 09:00 /sd (Get-Date).AddDays(1).ToString('yyyy/MM/dd') /sc once /f
```

**3. 验证：**
```bash
# 查看任务详情
schtasks /query /v /tn "早安提醒"
```

### 示例：每天晚上10点发送休息提醒

**1. 创建任务文件夹和批处理文件：**
```bash
mkdir "D:\AgentWorkspace\scheduled-tasks\bedtime_reminder"
```

```batch
@echo off
REM 保存为：D:\AgentWorkspace\scheduled-tasks\bedtime_reminder\bedtime_reminder.bat
REM 创建配置文件：bedtime_reminder\bedtime_reminder_config.txt（UTF-8 编码）
python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file "D:\AgentWorkspace\scheduled-tasks\bedtime_reminder\bedtime_reminder_config.txt" --user-id USER_DISCORD_ID >> D:\AgentWorkspace\scheduled-tasks\bedtime_reminder\bedtime_reminder.log 2>&1
exit /b 0
```

**2. 创建定时任务（每天晚上10点）：**
```bash
schtasks /create /tn "休息提醒" /tr "D:\AgentWorkspace\scheduled-tasks\bedtime_reminder\bedtime_reminder.bat" /st 22:00 /sc daily /f
```

**3. 验证和删除：**
```bash
# 立即执行测试
schtasks /run /tn "休息提醒"

# 不需要时删除任务
schtasks /delete /tn "休息提醒" /f
```

## 最佳实践总结

1. ✅ **每个任务独立文件夹** - 清晰管理 bat、txt、log 文件
2. ✅ **使用绝对路径** - 避免工作目录问题
3. ✅ **使用配置文件方式** - 完美支持中文和 emoji
4. ✅ **添加日志输出** - 方便调试和排查
5. ✅ **一次性任务测试** - 先验证再创建正式任务
6. ✅ **使用 PowerShell 计算时间** - 动态时间更灵活
7. ✅ **查看执行结果** - 确认任务成功执行
8. ✅ **及时删除无用任务** - 保持系统整洁
