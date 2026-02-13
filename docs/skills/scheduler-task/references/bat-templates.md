# Bat 脚本模板和示例

## 标准模板（推荐）

### 极简版本（所有参数在配置文件）

```batch
@echo off
REM ========================================
REM 任务名称：[任务描述]
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名]_config.txt"

exit /b 0
```

### 调试版本（带日志）

```batch
@echo off
REM ========================================
REM 任务名称：[任务描述]
REM 说明：添加日志输出便于调试
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名]_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\[任务名]\[任务名].log" 2>&1

exit /b 0
```

---

## 完整示例

### reminder 示例：橘子提醒

**文件：** `orange_reminder\orange_reminder.bat`

```batch
@echo off
REM ========================================
REM 任务名称：橘子提醒
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\orange_reminder\orange_reminder_config.txt"

exit /b 0
```

### task 示例：发送新闻汇总 PDF

**文件：** `send_news_pdf\send_news_pdf.bat`

```batch
@echo off
REM ========================================
REM 任务名称：发送新闻汇总PDF到Discord私聊
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\send_news_pdf\send_news_pdf_config.txt"

exit /b 0
```

### task 示例：数据备份并发送通知

**文件：** `backup_data\backup_data.bat`

```batch
@echo off
REM ========================================
REM 任务名称：数据备份并发送通知
REM 说明：所有参数由配置文件管理
REM ========================================

python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" --config-file "D:\AgentWorkspace\scheduled-tasks\backup_data\backup_data_config.txt" >> "D:\AgentWorkspace\scheduled-tasks\backup_data\backup_data.log" 2>&1

exit /b 0
```

---

## 常见问题排查

### 问题 1：Python 脚本找不到

**错误信息：**
```
'python' 不是内部或外部命令，也不是可运行的程序或批处理文件。
```

**解决方法：**
1. 确认 Python 安装路径
2. 使用完整路径或 python 替代 python
3. 示例：
```batch
C:\Users\ASUS\AppData\Local\Programs\Python\Python314\python.exe "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" ...
```

### 问题 2：路径包含空格导致错误

**错误信息：**
```
系统找不到指定的路径。
```

**解决方法：**
- 所有路径使用双引号括起来
- ✅ 正确：`python "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py"`
- ❌ 错误：`python D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py`

### 问题 3：配置文件路径错误

**错误信息：**
```
Config file not found: xxx_config.txt
```

**解决方法：**
- 确认配置文件使用绝对路径
- 确认配置文件存在
- 确认路径分隔符正确（Windows 使用反斜杠 \）
