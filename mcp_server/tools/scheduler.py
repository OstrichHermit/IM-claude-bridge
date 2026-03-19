"""
MCP 定时任务工具（纯文件操作版本）

MCP 工具只负责读写任务文件，实际的调度和执行由 Discord Bot 完成。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


# 任务文件路径（使用项目根目录的 shared）
TASKS_FILE = Path(__file__).parent.parent.parent / "shared" / "cron_jobs.json"


def _load_tasks() -> dict:
    """加载所有任务"""
    if not TASKS_FILE.exists():
        return {}

    try:
        with open(TASKS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {job['id']: job for job in data}
    except Exception:
        return {}


def _save_tasks(tasks: dict):
    """保存所有任务"""
    try:
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = list(tasks.values())
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise Exception(f"保存任务失败: {e}")


async def add_cron(
    cron_expr: str,
    content: str,
    username: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    tag: str = "task",
    description: str = "",
    repeat: bool = True
) -> str:
    """
    添加定时任务

    创建一个新的定时任务，按照 cron 表达式定时执行。
    任务会被写入共享文件，由 Discord Bot 读取并执行。

    Args:
        cron_expr: cron 表达式（必需），格式：分 时 日 月 周，例如 "0 9 * * *" 表示每天早上 9 点
        content: 任务内容/提示词（必需），任务执行时发送给 Claude 的内容
        username: 用户名（必需），任务关联的用户
        user_id: Discord 用户 ID（可选），私聊模式时使用
        channel_id: Discord 频道 ID（可选），频道模式时使用
        tag: 任务标签（可选），默认 "task"，可选值："task"（任务类）、"reminder"（提醒类）
        description: 任务描述（可选），用于识别任务
        repeat: 是否重复执行（可选），默认 true，false 表示一次性任务（执行后自动禁用）

    Returns:
        JSON 格式的创建结果，包含任务 ID

    Examples:
        # 每天早上 9 点发送报告（循环任务）
        add_cron(
            cron_expr="0 9 * * *",
            content="发送今日报告",
            username="鸵鸟居士",
            user_id="USER_DISCORD_ID",
            tag="task",
            description="每日报告"
        )

        # 2小时后提醒开会（一次性任务）
        add_cron(
            cron_expr="0 20 * * *",
            content="该开会啦",
            username="鸵鸟居士",
            user_id="USER_DISCORD_ID",
            tag="reminder",
            description="会议提醒",
            repeat=False
        )

        # 每小时提醒喝水（循环任务）
        add_cron(
            cron_expr="0 * * * *",
            content="该喝水了！",
            username="鸵鸟居士",
            user_id="USER_DISCORD_ID",
            tag="reminder"
        )

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - cron 表达式格式：分 时 日 月 周
        - 支持 cron 标准语法：* 表示任意，*/N 表示每 N，N-M 表示范围
        - 任务会由 Discord Bot 读取并执行，确保 Bot 正在运行
        - repeat=false 的任务执行一次后会自动禁用
        - 常用示例：
          * "0 9 * * *" - 每天早上 9 点
          * "*/30 * * * *" - 每 30 分钟
          * "0 */2 * * *" - 每 2 小时
          * "0 9 * * 1-5" - 周一到周五早上 9 点
    """
    try:
        # 加载现有任务
        tasks = _load_tasks()

        # 生成任务 ID
        job_id = str(uuid.uuid4())[:8]

        # 创建新任务
        job = {
            "id": job_id,
            "cron_expr": cron_expr,
            "content": content,
            "username": username,
            "user_id": user_id,
            "channel_id": channel_id,
            "tag": tag,
            "description": description or content[:50],
            "enabled": True,
            "repeat": repeat,  # 是否重复执行
            "created_at": datetime.now().isoformat(),
            "last_run": None,
            "last_error": None
        }

        # 保存任务
        tasks[job_id] = job
        _save_tasks(tasks)

        return json.dumps({
            "success": True,
            "job_id": job_id,
            "message": f"定时任务已创建: {job_id}"
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)


async def list_cron() -> str:
    """
    列出所有定时任务

    返回所有已创建的定时任务列表，包括启用和禁用的任务。

    Returns:
        JSON 格式的任务列表，每个任务包含：
        - id: 任务 ID
        - cron_expr: cron 表达式
        - content: 任务内容
        - username: 用户名
        - user_id: 用户 ID
        - channel_id: 频道 ID
        - tag: 标签
        - description: 描述
        - enabled: 是否启用
        - created_at: 创建时间
        - last_run: 最后运行时间
        - last_error: 最后错误信息

    Examples:
        # 列出所有任务
        result = await list_cron()
        tasks = json.loads(result)
        for task in tasks["jobs"]:
            print(f"{task['description']}: {task['cron_expr']}")
    """
    try:
        tasks = _load_tasks()

        return json.dumps({
            "success": True,
            "count": len(tasks),
            "jobs": list(tasks.values())
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)


async def delete_cron(job_id: str) -> str:
    """
    删除定时任务

    永久删除指定的定时任务，删除后无法恢复。

    Args:
        job_id: 任务 ID（必需），8 位字符

    Returns:
        JSON 格式的删除结果

    Examples:
        # 删除任务
        await delete_cron(job_id="a1b2c3d4")

    Note:
        - 删除操作不可逆，请谨慎操作
        - 如果任务 ID 不存在，会返回错误
        - Discord Bot 会自动检测到任务删除并停止调度
    """
    try:
        tasks = _load_tasks()

        if job_id not in tasks:
            return json.dumps({
                "success": False,
                "error": f"任务不存在: {job_id}"
            }, ensure_ascii=False)

        # 删除任务
        del tasks[job_id]
        _save_tasks(tasks)

        return json.dumps({
            "success": True,
            "message": f"任务已删除: {job_id}"
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)


async def toggle_cron(job_id: str, enabled: bool) -> str:
    """
    启用/禁用定时任务

    启用或禁用指定的定时任务，禁用后任务不会执行，但不会删除。

    Args:
        job_id: 任务 ID（必需），8 位字符
        enabled: 是否启用（必需），true 启用，false 禁用

    Returns:
        JSON 格式的操作结果

    Examples:
        # 启用任务
        await toggle_cron(job_id="a1b2c3d4", enabled=True)

        # 禁用任务
        await toggle_cron(job_id="a1b2c3d4", enabled=False)

    Note:
        - 禁用任务不会删除任务，可以重新启用
        - Discord Bot 会自动检测到任务状态变化并更新调度
        - 启用/禁用操作会在下次 Bot 扫描时生效（最多延迟 1 分钟）
    """
    try:
        tasks = _load_tasks()

        if job_id not in tasks:
            return json.dumps({
                "success": False,
                "error": f"任务不存在: {job_id}"
            }, ensure_ascii=False)

        # 更新任务状态
        tasks[job_id]['enabled'] = enabled
        _save_tasks(tasks)

        status = "启用" if enabled else "禁用"
        return json.dumps({
            "success": True,
            "message": f"任务已{status}: {job_id}"
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)


async def get_cron_info(job_id: str) -> str:
    """
    获取定时任务详情

    获取指定定时任务的详细信息，包括执行历史。

    Args:
        job_id: 任务 ID（必需），8 位字符

    Returns:
        JSON 格式的任务详情

    Examples:
        # 获取任务详情
        info = await get_cron_info(job_id="a1b2c3d4")
        task = json.loads(info)
        print(f"任务: {task['description']}")
        print(f"最后运行: {task['last_run']}")
    """
    try:
        tasks = _load_tasks()

        if job_id not in tasks:
            return json.dumps({
                "success": False,
                "error": f"任务不存在: {job_id}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "job": tasks[job_id]
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)


async def update_cron(
    job_id: str,
    cron_expr: Optional[str] = None,
    content: Optional[str] = None,
    description: Optional[str] = None,
    repeat: Optional[bool] = None,
    enabled: Optional[bool] = None
) -> str:
    """
    更新定时任务

    更新指定定时任务的部分或全部字段。只修改提供的参数，未提供的参数保持不变。

    Args:
        job_id: 任务 ID（必需），8 位字符
        cron_expr: cron 表达式（可选），格式：分 时 日 月 周
        content: 任务内容/提示词（可选），任务执行时发送给 Claude 的内容
        description: 任务描述（可选），用于识别任务
        repeat: 是否重复执行（可选），true 重复，false 一次性
        enabled: 是否启用（可选），true 启用，false 禁用

    Returns:
        JSON 格式的更新结果

    Examples:
        # 只修改执行时间
        await update_cron(job_id="a1b2c3d4", cron_expr="0 10 * * *")

        # 修改内容和描述
        await update_cron(
            job_id="a1b2c3d4",
            content="新的提醒内容",
            description="更新后的任务"
        )

        # 修改为一次性任务并禁用
        await update_cron(
            job_id="a1b2c3d4",
            repeat=False,
            enabled=False
        )

        # 同时修改多个字段
        await update_cron(
            job_id="a1b2c3d4",
            cron_expr="*/30 * * * *",
            content="每30分钟提醒",
            description="频繁提醒",
            repeat=True
        )

    Note:
        - 至少需要提供一个要修改的参数
        - 未提供的参数保持原值不变
        - 修改 cron_expr 会重新调度任务
        - 修改 enabled 会立即生效（需要 Bot 重新加载）
        - 修改 repeat 会影响任务执行后的行为
    """
    try:
        tasks = _load_tasks()

        if job_id not in tasks:
            return json.dumps({
                "success": False,
                "error": f"任务不存在: {job_id}"
            }, ensure_ascii=False)

        job = tasks[job_id]

        # 记录修改的字段
        changed_fields = []

        # 更新提供的字段
        if cron_expr is not None:
            job['cron_expr'] = cron_expr
            changed_fields.append('cron_expr')

        if content is not None:
            job['content'] = content
            changed_fields.append('content')

        if description is not None:
            job['description'] = description
            changed_fields.append('description')

        if repeat is not None:
            job['repeat'] = repeat
            changed_fields.append('repeat')

        if enabled is not None:
            job['enabled'] = enabled
            changed_fields.append('enabled')

        # 检查是否有修改
        if not changed_fields:
            return json.dumps({
                "success": False,
                "error": "没有提供任何要修改的参数"
            }, ensure_ascii=False)

        # 保存修改
        _save_tasks(tasks)

        return json.dumps({
            "success": True,
            "message": f"任务已更新: {job_id}",
            "job_id": job_id,
            "changed_fields": changed_fields
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)
