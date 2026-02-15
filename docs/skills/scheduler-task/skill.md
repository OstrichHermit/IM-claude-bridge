---
name: scheduler-task-v2
description: 创建和管理 Windows 计划任务，包括编写异步执行的 bat 脚本、配置 Discord Bridge trigger_scheduled_task.py 参数、避免任务阻塞等最佳实践。当用户需要创建定时任务、设置提醒、编写 bat 脚本触发 Python 脚本、或配置自动化工作流时使用此 Skill 。
---

# Windows 定时任务系统管理

## Overview

创建和管理 Windows 计划任务的完整工作流，支持一次性任务和循环任务。

**核心理念：** 定时任务不直接执行操作，而是触发一个新的 Claude Code 会话。新会话收到配置文件中的 `content` 作为提示词，Claude Code 根据提示词执行实际操作。

**核心能力：**
- 创建一次性/循环定时任务
- 编写异步执行的 bat 脚本（避免阻塞）
- 配置 Discord Bridge `trigger_scheduled_task.py` 参数
- 完美支持中文字符（UTF-8 配置文件）

**何时使用此 Skill：**
- 用户需要创建定时任务（一次性或循环）
- 用户需要设置提醒（健康提醒、日程提醒、待办事项）
- 用户需要定时发送文件到 Discord
- 用户需要定时运行脚本或命令

---

## 快速开始

### 一次性任务

**用户请求：** "2分钟后发送这个PDF给我"

**工作流程：**
1. 创建任务文件夹和配置文件
2. 创建 bat 脚本（只需指定配置文件路径）
3. 使用 `schtasks` 命令创建任务
4. 指定执行时间 `/st`
5. 设置 `/sc once` 参数

**示例：2分钟后执行**
```powershell
schtasks /create /tn "任务名称" /tr "D:\path\to\script.bat" /st (Get-Date).AddMinutes(2).ToString('HH:mm') /sc once /f
```

### 循环任务

**用户请求：** "每天上午9点发送报告"

**工作流程：**
1. 创建任务文件夹和配置文件
2. 创建 bat 脚本
3. 使用 `schtasks` 命令创建任务
4. 设置 `/sc daily` / `/sc hourly` / `/sc weekly`
5. 指定具体时间参数

**示例：每天上午9点执行**
```powershell
schtasks /create /tn "任务名称" /tr "D:\path\to\script.bat" /st 09:00 /sc daily /f
```

---

## 工作原理

### 定时任务的完整执行流程

```
┌─────────────────┐
│ 1. 时间到        │ Windows 计划任务触发
└────────┬────────┘
         ↓
┌─────────────────┐
│ 2. 执行 bat      │ 运行批处理脚本
└────────┬────────┘
         ↓
┌─────────────────┐
│ 3. 调用 Python   │ trigger_scheduled_task.py 读取配置文件
└────────┬────────┘
         ↓
┌─────────────────┐
│ 4. 启动新会话     │ 创建新的 Claude Code 会话
└────────┬────────┘
         ↓
┌─────────────────┐
│ 5. 发送提示词     │ 将 content 内容发送给新会话
└────────┬────────┘
         ↓
┌─────────────────┐
│ 6. Claude 执行   │ 新会话中的 Claude 根据提示词执行操作
└─────────────────┘
```

### 关键理解点

**❌ 错误理解：**
```
定时任务 → 直接发送文件/消息 → 完成
```

**✅ 正确理解：**
```
定时任务 → 触发新 Claude Code 会话 → 会话收到提示词 → Claude 执行操作
```

---

## 配置文件格式

### 标准格式（ini 风格）

**文件位置：** `任务名\任务名_config.txt`

**编码要求：** ⚠️ 必须使用 UTF-8 编码，否则中文会乱码

#### 格式说明

```ini
# 任务配置文件（使用 UTF-8 编码）
# 参数说明：
# - username: 用户名
# - content: 提示词内容（使用 <<<MARKER_START 和 <<<MARKER_END 包裹）
# - user_id: Discord 用户 ID（私聊模式必填）
# - channel_id: Discord 频道 ID（频道模式必填）
# - tag: 消息标签（reminder=提醒类，task=任务类）

username=鸵鸟居士
content<<<MARKER_START
[这里写内容，支持多行]
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

**重要提示：**
- `content` 字段必须使用标记包裹：`content<<<MARKER_START` ... `<<<MARKER_END`
- 标记中间的内容会完整保留换行和格式
- 开始标记：`key<<<MARKER_START`（注意：key 后无等号）
- 结束标记：`<<<MARKER_END`（独占一行）

### 支持的参数

| 参数 | 说明 | 必填 | reminder 示例 | task 示例                              |
|------|------|------|-------------|--------------------------------------|
| `username` | 用户名 | ✅ | `鸵鸟居士` | `鸵鸟居士`                               |
| `content` | 提示词内容（使用标记包裹） | ✅ | 见下方示例 | 见下方示例                                 |
| `user_id` | Discord 用户 ID | * | `USER_DISCORD_ID` | （留空）                                 |
| `channel_id` | Discord 频道 ID | * | (留空) | `1466858871720251425`                |
| `tag` | 消息标签 | ✅ | `reminder` | `task`                               |

* 注：`user_id` 和 `channel_id` 必须二选一，不能同时为空或同时填写。

### 编码要求

- ⚠️ **配置文件必须使用 UTF-8 编码**
- Windows 记事本默认使用 ANSI，会导致中文乱码
- 推荐使用 VS Code、Notepad++ 等编辑器，确保保存为 UTF-8

---

## content 参数编写指南

### reminder 类型（提醒类）

**目的：** 直接发送提醒消息

**编写要点：**
- 直接写最终要发送的消息内容
- 可以包含 emoji、换行、格式化文本
- 简洁明了，一眼就能看懂

**示例：**

**早上起床提醒：**
```ini
content<<<MARKER_START
早上好！☀️ 美好的一天开始了，记得吃早餐哦～
<<<MARKER_END
```

**喝水提醒：**
```ini
content<<<MARKER_START
鸵鸟居士，该喝水了！💧 保持健康，多喝温水～
<<<MARKER_END
```

### task 类型（任务类）

**目的：** 提示新会话执行某个操作

**编写要点：**
- 明确操作类型（"使用 Discord MCP"、"运行脚本"、"读取文件"）
- 指定完整路径（"files/新闻汇总.pdf" 而非 "PDF"）
- 包含必要参数（user_id、channel_id、文件名、命令参数等）
- 描述要详细但简洁
- 不要写执行后的结果消息（如"PDF已送达"），这是执行结果不是指令

**标准格式：**
```
content<<<MARKER_START
[操作类型] [目标对象] [参数列表]
<<<MARKER_END
```

**示例对照：**

❌ **错误写法：**
```ini
content=把PDF发给我
```

✅ **正确写法：**
```ini
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 files 文件夹下面的 新闻汇总_2026-02-09.pdf 文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID
<<<MARKER_END
```

**task 类型 content 检查清单：**
```
□ 是否明确使用哪个工具（Discord MCP、powershell、Read、Write）？
□ 是否指定完整路径（files/xxx.pdf 而非 xxx.pdf）？
□ 是否包含所有必要参数（user_id、channel_id、文件路径）？
□ 是否描述了具体操作（发送、读取、运行）？
□ 内容是否简洁但足够详细？
```

---

## Bat 脚本模板

### 标准模板（推荐）

**极简版本（所有参数在配置文件）：**
```batch
@echo off
REM ========================================
REM 任务名称：[任务描述]
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名]_config.txt"

exit /b 0
```

**调试版本（带日志）：**
```batch
@echo off
REM ========================================
REM 任务名称：[任务描述]
REM 说明：添加日志输出便于调试
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名]_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名].log" 2>&1

exit /b 0
```

### 关键点

- ⚠️ **使用绝对路径** - Windows 计划任务的工作目录可能不稳定
- ⚠️ **避免中文字符在文件名中** - 会导致命令截断
- ✅ **使用配置文件存储所有内容** - UTF-8 编码的 .txt 文件
- ✅ **每个任务独立文件夹** - 清晰管理 bat、txt、log 三个文件
- ✅ **日志重定向** - 方便调试和排查问题

---

## 文件夹结构规范

### 标准目录结构

```
D:\AgentWorkspace\scheduled-tasks\
├── orange_reminder\                    # 橘子提醒（reminder 类型）
│   ├── orange_reminder.bat              # 批处理脚本
│   ├── orange_reminder_config.txt         # 任务配置（UTF-8）
│   └── orange_reminder.log               # 执行日志
├── send_pdf\                          # 发送 PDF（task 类型）
│   ├── send_pdf.bat
│   ├── send_pdf_config.txt
│   └── send_pdf.log
├── morning_reminder\                   # 早安提醒（reminder 类型）
│   ├── morning_reminder.bat
│   ├── morning_reminder_config.txt
│   └── morning_reminder.log
└── backup_data\                       # 数据备份（task 类型）
    ├── backup_data.bat
    ├── backup_data_config.txt
    └── backup_data.log
```

### 路径规范

- **任务文件夹：** `D:\AgentWorkspace\scheduled-tasks\任务名\`
  - 每个定时任务一个独立文件夹
  - 文件夹命名：英文（避免中文字符）
- **批处理文件：** `任务名\任务名.bat`
- **配置文件：** `任务名\任务名_config.txt`（UTF-8 编码）
- **日志文件：** `任务名\任务名.log`
- **Python 脚本位置：** 使用绝对路径 `D:\AgentWorkspace\discord-claude-bridge\`

---

## Windows 计划任务工具参考

详细的 `schtasks` 命令参考、常见调度模式和故障排查指南，请参考：

**查看完整参考：** `references/schtasks-reference.md`

**快速参考：**
- **创建任务：** `schtasks /create /tn "任务名" /tr "脚本路径" /st "时间" /sc "类型" /f`
- **删除任务：** `schtasks /delete /tn "任务名" /f`
- **查询任务：** `schtasks /query /v /tn "任务名"`

---

## ⚠️ 环境要求

### 必须使用 PowerShell

**重要**：schtasks 命令必须在 **PowerShell** 中执行，不要在 Git Bash 或其他 shell 中使用！

**原因**：
- Git Bash 对 Windows 原生命令支持不好（路径映射、参数解析问题）
- PowerShell 是 Windows 原生命令，对 schtasks 支持最好
- 避免路径、转义字符等常见问题

**如何确认在 PowerShell 中**：
- 提示符前缀：`PS C:\>` 而不是 `$` 或 `bash`
- 或者在 Windows Terminal 中选择 "PowerShell" 标签页

**正确示例**：
```powershell
PS C:\> schtasks /create /tn "心跳检查" /tr "python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py --config-file D:\AgentWorkspace\scheduled-tasks\daily_heartbeat\daily_heartbeat_config.txt" /st 09:37 /sc hourly /f
```

**错误示例**（Git Bash 中）：
```bash
# ❌ 不要在 Git Bash 中执行
schtasks /create /tn "心跳检查" ...
# 会报错：ERROR: Invalid argument/option
```

---

## 完整示例

详细的配置文件示例、bat 脚本示例和创建命令示例，请参考：

**查看完整示例：** `references/bat-templates.md`

**快速示例：**

### reminder 示例：橘子提醒

```ini
# orange_reminder_config.txt
username=鸵鸟居士
content<<<MARKER_START
鸵鸟居士，该去吃橘子了！🍊
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=reminder
```

```batch
# orange_reminder.bat
@echo off
python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder_config.txt"
exit /b 0
```

### task 示例：科技新闻汇总（多行内容）

```ini
# tech_news_report_config.txt
username=鸵鸟居士
content<<<MARKER_START
执行以下操作：
1. 使用 TrendRadar MCP 工具搜索近两日的科技新闻（关键词：科技、AI、芯片、互联网、手机、电脑）
2. 使用 resolve_date_range 工具获取"近两日"的精确日期范围
3. 使用 search_news 工具搜索科技新闻，参数：query="科技 OR AI OR 芯片 OR 互联网 OR 手机 OR 电脑"，include_url=true，limit=100
4. 汇总搜索结果，生成一份 Markdown 格式的科技新闻报告
5. 使用 Write 工具将报告保存到 D:\AgentWorkspace\files\科技新闻汇总_[当前日期].md 文件
6. 使用 Discord MCP 工具的 send_file_to_discord 函数，把刚生成的报告文件发送到我的 Discord 私聊，user_id 是 USER_DISCORD_ID，使用 embed=true
7. 完成后使用 Discord MCP 发送一条确认消息"✅ 科技新闻报告已生成并发送"

注意：
- 报告要包含新闻标题、来源平台、发布时间、简要描述
- 按重要性和热度排序
- 添加清晰的标题和日期
<<<MARKER_END
user_id=USER_DISCORD_ID
channel_id=
tag=task
```

```batch
# send_news_pdf.bat
@echo off
python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\send_news_pdf\send_news_pdf_config.txt"
exit /b 0
```

---

## 调试和故障排查

### 查看执行日志

**检查日志文件：**
```powershell
# 如果脚本配置了日志重定向
type D:\AgentWorkspace\scheduled-tasks\task_name.log
```

**查看任务执行历史：**
```powershell
# 查看上次运行时间和结果
schtasks /query /v /tn "任务名称" | findstr "Last Run Time Last Result"
```

### 常见问题排查

**问题 1：任务显示已执行，但无效果**
- 检查批处理脚本是否可执行
- 查看日志文件是否有错误信息
- 确认路径是否使用绝对路径
- 验证配置文件格式是否正确（key=value）
- 检查 tag 是否正确（reminder 还是 task）
- 如果是 task，检查 content 是否是有效的执行指令

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

**问题 4：task 类型任务未执行**
- 确认 content 是执行指令而非结果消息
- 检查指令是否明确（使用哪个工具、什么参数）
- 验证路径和参数是否正确
- 查看日志确认新会话是否成功启动

---

## 快速参考

### reminder vs task 对照表

| 维度 | reminder（提醒类） | task（任务类） |
|------|-----------------|---------------|
| **tag** | `tag=reminder` | `tag=task` |
| **content 含义** | 最终发送的消息 | 给新会话的执行指令 |
| **content 示例** | 使用标记包裹（见上方示例） | 使用标记包裹（见上方示例） |
| **新会话行为** | 原样发送 content | 理解并执行 content 中的指令 |
| **适用场景** | 健康提醒、日程提醒、待办事项 | 执行脚本、发送文件、运行命令、数据备份 |

### task 类型 content 编写模板

所有模板必须使用标记包裹：

**发送文件：**
```ini
content<<<MARKER_START
使用 Discord MCP 工具的 send_file_to_discord 函数，把 [文件路径] 文件发送到我的 Discord 私聊，user_id 是 [你的用户ID]
<<<MARKER_END
```

**运行脚本：**
```ini
content<<<MARKER_START
使用 powershell 工具运行 [脚本路径]，参数是 [参数列表]
<<<MARKER_END
```

**组合操作：**
```ini
content<<<MARKER_START
先使用 powershell 工具运行 [脚本路径]，等待执行完成后，使用 Discord MCP 工具发送文件"[文件路径]"到我的私聊，user_id 是 [你的用户ID]
<<<MARKER_END
```

---

## Resources

### references/

此 Skill 包含详细的参考文档，在需要时加载：

- **`references/bat-templates.md`** - 各种场景的 bat 脚本模板和示例
- **`references/config-examples.md`** - reminder 和 task 类型的配置文件示例
- **`references/schtasks-reference.md`** - Windows schtasks 命令完整参考

**何时加载：**
- 需要具体示例时，加载 `bat-templates.md` 或 `config-examples.md`
- 需要高级 schtasks 功能或故障排查时，加载 `schtasks-reference.md`