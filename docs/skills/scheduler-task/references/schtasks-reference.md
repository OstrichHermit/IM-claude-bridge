# Windows 计划任务（schtasks）完整参考

## 创建任务

### 基本语法

```bash
schtasks /create /tn "任务名称" /tr "要执行的命令" /st "开始时间" /sc "调度类型" /f
```

### 参数说明

| 参数 | 说明 | 必填 | 示例 |
|------|------|------|------|
| `/tn <名称>` | 任务名称 | ✅ | `send_pdf`, `orange_reminder` |
| `/tr <命令>` | 要执行的命令或批处理文件路径 | ✅ | `D:\AgentWorkspace\scheduled-tasks\send_pdf\send_pdf.bat` |
| `/st <时间>` | 开始时间，格式 HH:mm | ✅ | `09:00`, `14:30` |
| `/sd <日期>` | 开始日期，格式 yyyy/MM/dd（可选） | ❌ | `2026/02/14` |
| `/sc <类型>` | 调度类型 | ✅ | `once`, `daily`, `weekly` |
| `/d <星期>` | 星期几（仅 weekly） | ❌ | `MON`, `TUE`, `WED`, `THU`, `FRI`, `SAT`, `SUN` |
| `/f` | 强制创建（覆盖已存在的同名任务） | ❌ | 无需参数 |

### 调度类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `once` | 执行一次 | 2分钟后执行 |
| `daily` | 每天 | 每天上午9点执行 |
| `weekly` | 每周 | 每周一下午2点执行 |
| `monthly` | 每月 | 每月1号凌晨执行 |
| `hourly` | 每小时 | 每小时检查一次状态 |

---

## 使用 PowerShell 计算动态时间

### 基本语法

```powershell
# 使用 PowerShell 命令
schtasks /create /tn "任务名" /tr "D:\script.bat" /st (Get-Date).AddMinutes(1).ToString('HH:mm') /sc once /f
```

### 常用时间计算

**1 分钟后执行：**
```powershell
(Get-Date).AddMinutes(1).ToString('HH:mm')
```

**2 小时后执行：**
```powershell
(Get-Date).AddHours(2).ToString('HH:mm')
```

**明天上午9点执行：**
```powershell
# 使用 /sd 参数指定日期
schtasks /create /tn "任务名" /tr "D:\script.bat" /st 09:00 /sd (Get-Date).AddDays(1).ToString('yyyy/MM/dd') /sc once /f
```

**下周一上午10点执行：**
```powershell
schtasks /create /tn "任务名" /tr "D:\script.bat" /st 10:00 /sd (Get-Date).AddDays(7).ToString('yyyy/MM/dd') /sc weekly /d MON /f
```

---

## 常用调度示例

### 一次性任务

**1 分钟后执行：**
```bash
schtasks /create /tn "临时任务" /tr "D:\script.bat" /st 10:30 /sc once /f
```

**2 分钟后执行（PowerShell 动态计算）：**
```powershell
schtasks /create /tn "临时任务" /tr "D:\script.bat" /st (Get-Date).AddMinutes(2).ToString('HH:mm') /sc once /f
```

**指定日期时间执行（明天上午9点）：**
```bash
schtasks /create /tn "定时任务" /tr "D:\script.bat" /st 09:00 /sd 2026/02/14 /sc once /f
```

### 循环任务

**每 天上午9点执行：**
```bash
schtasks /create /tn "每日报告" /tr "D:\script.bat" /st 09:00 /sc daily /f
```

**每 小时执行一次：**
```bash
schtasks /create /tn "每小时检查" /tr "D:\script.bat" /sc hourly /f
```

**每 周一上午10点执行：**
```bash
schtasks /create /tn "周报" /tr "D:\script.bat" /st 10:00 /sc weekly /d MON /f
```

**每 月1号执行：**
```bash
schtasks /create /tn "月度总结" /tr "D:\script.bat" /st 00:00 /sc monthly /d 1 /f
```

---

## 查询和管理任务

### 查询任务详情

**列出所有任务：**
```bash
schtasks /query
```

**查看特定任务的详细信息：**
```bash
schtasks /query /v /tn "任务名称"
```

**使用 PowerShell 格式化输出：**
```bash
schtasks /query /v /tn "任务名称" | Format-List
```

### 立即执行任务

```bash
schtasks /run /tn "任务名称"
```

### 删除任务

```bash
schtasks /delete /tn "任务名称" /f
```

### 禁用/启用任务

**禁用任务：**
```bash
schtasks /change /tn "任务名称" /disable
```

**启用任务：**
```bash
schtasks /change /tn "任务名称" /enable
```

---

## 常见问题和解决方法

### 问题 1：任务显示"已执行"，但无效果

**可能原因：**
1. 批处理脚本路径错误
2. 配置文件格式错误
3. Python 脚本路径错误
4. 配置文件编码不是 UTF-8

**排查步骤：**
1. 手动运行批处理脚本，查看是否有错误信息
2. 检查批处理脚本路径是否使用绝对路径
3. 检查配置文件格式是否正确（key=value）
4. 检查配置文件编码是否为 UTF-8
5. 查看日志文件（如果配置了日志重定向）

### 问题 2：配置文件参数不生效

**可能原因：**
1. 配置文件编码不是 UTF-8
2. 参数格式错误（等号两边有空格）
3. 必填参数未填写
4. user_id 和 channel_id 同时填写或同时为空

**排查步骤：**
1. 使用 VS Code 打开配置文件，查看右下角编码是否为 UTF-8
2. 确认参数格式：`key=value`（等号两边无空格）
3. 验证必填参数是否都提供：username、content、tag
4. 验证 user_id 或 channel_id 二选一

### 问题 3：计划任务创建失败

**可能原因：**
1. 路径包含特殊字符
2. 批处理文件不存在
3. 时间格式错误
4. 任务名称已存在且未使用 /f 参数

**排查步骤：**
1. 检查路径是否包含特殊字符（如中文、emoji）
2. 确认批处理文件存在
3. 使用 `/f` 参数强制覆盖
4. 验证时间格式是否正确（HH:mm）

### 问题 4：Python 脚本找不到

**可能原因：**
1. Python 未安装或路径错误
2. Python 命令不在系统 PATH 中
3. trigger_scheduled_task.py 脚本路径错误

**排查步骤：**
1. 运行 `python --version` 或 `python3 --version` 检查 Python 是否安装
2. 使用完整路径调用 Python：
   ```batch
   C:\Users\ASUS\AppData\Local\Programs\Python\Python314\python.exe "D:\AgentWorkspace\discord-claude-bridge\trigger_scheduled_task.py" ...
   ```
3. 验证 trigger_scheduled_task.py 脚本路径存在

### 问题 5：task 类型任务未执行

**可能原因：**
1. content 不是执行指令，而是执行结果
2. content 未明确指定使用哪个工具
3. content 未指定完整路径
4. content 未包含必要参数

**排查步骤：**
1. 确认 content 是执行指令而非结果消息
2. 检查指令是否明确（使用哪个工具、什么参数）
3. 验证路径和参数是否正确
4. 查看日志确认新会话是否成功启动

---

## 高级用法

### 组合使用 PowerShell 和批处理

**创建 2 分钟后执行的任务（使用 PowerShell 计算）：**

```batch
@echo off
setlocal

REM 计算2分钟后的时间
for /f "tokens=1-3 delims=:." %%a in ("%time%") do (
    set /a "hour=%%a"
    set /a "minute=%%b+2"
)

REM 处理分钟进位
if %minute% geq 60 (
    set /a "minute-=60"
    set /a "hour+=1"
)

REM 处理小时进位
if %hour% geq 24 set /a "hour-=24"

REM 格式化时间（补零）
if %hour% lss 10 set hour=0%hour%
if %minute% lss 10 set minute=0%minute%

set exec_time=%hour%:%minute%

schtasks /create /tn "任务名称" /tr "D:\script.bat" /st %exec_time% /sc once /f

endlocal
```

### 使用任务触发器模式

**创建触发任务（每天运行，用于创建其他临时任务）：**

```bash
schtasks /create /tn "任务触发器" /tr "D:\AgentWorkspace\scheduled-tasks\trigger_tasks.bat" /st 00:00 /sc daily /f
```

**trigger_tasks.bat 内容：**
```batch
@echo off
REM 这里可以包含逻辑，根据当前日期/时间创建不同的临时任务

REM 示例：每周一早上创建临时任务
...
```

---

## schtasks 命令速查表

| 操作 | 命令 |
|------|------|
| 创建任务 | `schtasks /create /tn "名" /tr "路径" /st "时间" /sc "类型" /f` |
| 删除任务 | `schtasks /delete /tn "名" /f` |
| 查询任务 | `schtasks /query [/v] [/tn "名"]` |
| 运行任务 | `schtasks /run /tn "名"` |
| 禁用任务 | `schtasks /change /tn "名" /disable` |
| 启用任务 | `schtasks /change /tn "名" /enable` |
| 一分钟后执行 | `/st (Get-Date).AddMinutes(1).ToString('HH:mm')` |
| 2分钟后执行 | `/st (Get-Date).AddMinutes(2).ToString('HH:mm')` |
| 每天执行 | `/sc daily /st 09:00` |
| 每 小时执行 | `/sc hourly` |
| 每 周执行 | `/sc weekly /d MON /st 10:00` |
| 每 月执行 | `/sc monthly /d 1 /st 00:00` |
| 明天执行 | `/sd (Get-Date).AddDays(1).ToString('yyyy/MM/dd')` |
| 下周执行 | `/sd (Get-Date).AddDays(7).ToString('yyyy/MM/dd')` |
