"""
Discord Bridge MCP Server - 基于消息队列的架构

为 Claude Code CLI 提供 Discord 文件发送功能。
通过消息队列与 Discord Bot 通信，无需创建新的 Discord 客户端。

架构：
    Claude Code CLI
        ↓ (MCP 协议)
    MCP Server (工具层)
        ↓ (写入请求)
    MessageQueue (SQLite 消息队列)
        ↓ (轮询处理)
    Discord Bot (已有进程)
        ↓ (Discord API)
    Discord 用户/频道
"""
import asyncio
import io
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

# pythonw.exe 下 sys.stdout/stderr 为 None，会导致 Uvicorn 日志格式化崩溃
# 兜底处理：替换为 devnull 避免 AttributeError: 'NoneType' object has no attribute 'isatty'
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

from fastmcp import FastMCP

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger import get_logger
from shared.config import Config

log = get_logger("MCPServer", "mcp_server")


# 捕获 pythonw.exe 中丢失的未处理异常
def _global_excepthook(exc_type, exc_value, exc_tb):
    """全局异常钩子：把未捕获的异常写入日志文件"""
    tb_text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.log(f"❌ 未捕获的异常（进程即将退出）:\n{tb_text}")

sys.excepthook = _global_excepthook

from mcp_server.tools import _send_files
from mcp_server.tools.scheduler import (
    add_cron as _add_cron_impl,
    list_cron as _list_cron_impl,
    delete_cron as _delete_cron_impl,
    toggle_cron as _toggle_cron_impl,
    get_cron_info as _get_cron_info_impl,
    update_cron as _update_cron_impl
)
from mcp_server.tools.time import (
    get_current_time as _get_current_time_impl
)


# 创建 FastMCP 应用
mcp = FastMCP('im-claude-bridge')


# ==================== MCP 工具 ====================

@mcp.tool
async def send_files(
    file_paths: list
) -> str:
    """
    发送文件到当前对话（支持 Discord 和微信）

    将本地文件发送到当前对话的频道或聊天中。
    通过 SessionWorker 统一消息队列发送，文件会按序与其他消息一起发送。

    Args:
        file_paths: 要发送的文件路径列表（必需）

    Returns:
        JSON格式的发送结果，包含成功状态和文件信息

    Examples:
        # 发送单个文件
        send_files(file_paths=["chart.png"])

        # 发送多个文件
        send_files(file_paths=["chart1.png", "chart2.png", "report.pdf"])

    Note:
        - 文件会发送到当前对话所在的频道或聊天
        - Discord 限制单次最多发送 10 个文件
        - 微信限制单次最多发送 9 个文件
        - 支持格式：图片、PDF、文本、压缩包等
        - 不存在的文件会被自动跳过
    """
    return await _send_files(file_paths=file_paths)


@mcp.tool
async def add_cron(
    cron_expr: str,
    content: str,
    username: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    tag: str = "task",
    channel_type: str = "discord",
    description: str = "",
    repeat: bool = True
) -> str:
    """
    添加定时任务

    创建一个新的定时任务，按照 cron 表达式定时执行。

    Args:
        cron_expr: cron 表达式（必需），格式：分 时 日 月 周，例如 "0 9 * * *" 表示每天早上 9 点
        content: 任务内容/提示词（必需），任务执行时发送给 Claude 的内容
        username: 用户名（必需），任务关联的用户
        user_id: Discord 用户 ID（可选），私聊模式时使用
        channel_id: Discord 频道 ID（可选），频道模式时使用
        tag: 任务标签（可选），默认 "task"，可选值："task"（任务类）、"reminder"（提醒类）
        channel_type: 频道类型（可选），默认 "discord"，可选值："discord"、"weixin"
        description: 任务描述（可选），用于识别任务
        repeat: 是否重复执行（可选），默认 true，false 表示一次性任务（执行后自动禁用）

    Returns:
        JSON 格式的创建结果，包含任务 ID

    Examples:
        # 每天早上 9 点发送报告到 Discord（循环任务）
        add_cron(
            cron_expr="0 9 * * *",
            content="发送今日报告",
            username="用户名",
            user_id="USER_DISCORD_ID",
            channel_type="discord",
            tag="task",
            description="每日报告"
        )

        # 每天早上 9 点发送报告到微信（循环任务）
        add_cron(
            cron_expr="0 9 * * *",
            content="发送今日报告",
            username="用户名",
            user_id="USER_WEIXIN_ID",
            channel_type="weixin",
            tag="task",
            description="每日报告"
        )

        # 2小时后提醒开会（一次性任务）
        add_cron(
            cron_expr="0 20 * * *",
            content="该开会啦",
            username="用户名",
            user_id="USER_DISCORD_ID",
            tag="reminder",
            description="会议提醒",
            repeat=False
        )

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - channel_type 指定发送到哪个频道：discord 或 weixin（默认 discord）
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
    return await _add_cron_impl(
        cron_expr=cron_expr,
        content=content,
        username=username,
        user_id=user_id,
        channel_id=channel_id,
        tag=tag,
        channel_type=channel_type,
        description=description,
        repeat=repeat
    )


@mcp.tool
async def list_cron() -> str:
    """
    列出所有定时任务

    返回所有已创建的定时任务列表，包括启用和禁用的任务。

    Returns:
        JSON 格式的任务列表

    Examples:
        # 列出所有任务
        result = await list_cron()
    """
    return await _list_cron_impl()


@mcp.tool
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
    """
    return await _delete_cron_impl(job_id)


@mcp.tool
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
        - 启用/禁用操作立即生效
    """
    return await _toggle_cron_impl(job_id, enabled)


@mcp.tool
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
    """
    return await _get_cron_info_impl(job_id)


@mcp.tool
async def update_cron(
    job_id: str,
    cron_expr: Optional[str] = None,
    content: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    tag: Optional[str] = None,
    channel_type: Optional[str] = None,
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
        username: 用户名（可选），任务关联的用户
        user_id: Discord 用户 ID（可选），私聊模式时使用
        channel_id: Discord 频道 ID（可选），频道模式时使用
        tag: 任务标签（可选），如 "task" 或 "reminder"
        channel_type: 频道类型（可选），"discord" 或 "weixin"
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

        # 修改为微信任务
        await update_cron(
            job_id="a1b2c3d4",
            channel_type="weixin",
            user_id="USER_WEIXIN_ID"
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
        - user_id 和 channel_id 必须二选一，不能同时填写
        - channel_type 指定发送到哪个频道：discord 或 weixin
        - 至少需要提供一个要修改的参数
        - 未提供的参数保持原值不变
        - 修改 cron_expr 会重新调度任务
        - 修改 enabled 会立即生效（需要 Bot 重新加载）
        - 修改 repeat 会影响任务执行后的行为
    """
    return await _update_cron_impl(
        job_id=job_id,
        cron_expr=cron_expr,
        content=content,
        username=username,
        user_id=user_id,
        channel_id=channel_id,
        tag=tag,
        channel_type=channel_type,
        description=description,
        repeat=repeat,
        enabled=enabled
    )


@mcp.tool
async def get_current_time(timezone: str = "Asia/Taipei") -> str:
    """
    获取当前时间

    获取指定时区的当前时间。

    Args:
        timezone: 时区（可选），默认 "Asia/Taipei"
            常用时区：
            - "Asia/Taipei" - 台北时间
            - "Asia/Shanghai" - 上海时间
            - "Asia/Hong_Kong" - 香港时间
            - "Asia/Tokyo" - 东京时间
            - "America/New_York" - 纽约时间
            - "Europe/London" - 伦敦时间
            - "UTC" - 协调世界时

    Returns:
        JSON 格式的时间信息，包含：
        - success: 是否成功
        - timezone: 使用的时区
        - datetime: 当前日期时间（YYYY-MM-DD HH:MM:SS）
        - timestamp: Unix 时间戳
        - date: 日期（YYYY-MM-DD）
        - time: 时间（HH:MM:SS）
        - unix_timestamp: Unix 时间戳（秒）

    Examples:
        # 获取台北时间（默认）
        result = await get_current_time()

        # 获取上海时间
        result = await get_current_time(timezone="Asia/Shanghai")

        # 获取 UTC 时间
        result = await get_current_time(timezone="UTC")

    Note:
        - 支持所有 IANA 时区标识符
        - 如果时区无效，会返回错误信息
        - 返回的时间格式为 ISO 8601 标准
    """
    return await _get_current_time_impl(timezone)


# ==================== 启动入口 ====================

def run_server(
    transport: str = None,
    host: str = None,
    port: int = None
):
    """
    启动 MCP 服务器

    Args:
        transport: 传输模式，'stdio' 或 'http'，默认从配置文件读取
        host: HTTP模式的监听地址，默认从配置文件读取
        port: HTTP模式的监听端口，默认从配置文件读取
    """
    # 从配置文件读取默认值
    config = Config()

    if transport is None:
        transport = config.mcp_transport
    if host is None:
        host = config.mcp_host
    if port is None:
        port = config.mcp_port

    log.log("")
    log.log("=" * 60)
    log.log("  Discord Bridge MCP Server (基于消息队列)")
    log.log("=" * 60)
    log.log(f"  传输模式: {transport.upper()}")

    if transport == 'stdio':
        log.log("  协议: MCP over stdio (标准输入输出)")
    elif transport == 'http':
        log.log(f"  协议: MCP over HTTP")
        log.log(f"  服务器监听: {host}:{port}")

    log.log("")
    log.log("  已注册的工具:")
    log.log("    文件发送:")
    log.log("      1. send_files                   - 发送文件到当前对话（支持多文件）")
    log.log("    定时任务:")
    log.log("      2. add_cron                     - 添加定时任务")
    log.log("      3. list_cron                    - 列出所有定时任务")
    log.log("      4. delete_cron                  - 删除定时任务")
    log.log("      5. toggle_cron                  - 启用/禁用定时任务")
    log.log("      6. get_cron_info                - 获取定时任务详情")
    log.log("      7. update_cron                  - 更新定时任务")
    log.log("    其他:")
    log.log("      8. get_current_time             - 获取当前时间（支持多时区）")
    log.log("")
    log.log("  架构说明:")
    log.log("    - MCP Server 通过消息队列与 Discord/微信 Bot 通信")
    log.log("    - 无需创建新的客户端")
    log.log("    - 需要确保对应的 Bot 正在运行")
    log.log("    - 定时任务由 Discord Bot 调度执行")
    log.log("=" * 60)
    log.log("")

    # 捕获 asyncio 中未处理的异常
    def _async_exception_handler(loop, context):
        """asyncio 异常处理器：把未处理的异步异常写入日志"""
        exception = context.get('exception')
        message = context.get('message', '')
        if exception:
            tb_text = ''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))
            log.log(f"❌ asyncio 未处理异常: {message}\n{tb_text}")
        else:
            log.log(f"❌ asyncio 错误: {message}")

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_async_exception_handler)
    except RuntimeError:
        pass

    # 根据传输模式运行服务器
    try:
        if transport == 'stdio':
            mcp.run(transport='stdio')
        elif transport == 'http':
            mcp.run(
                transport='http',
                host=host,
                port=port,
                path='/mcp'
            )
        else:
            raise ValueError(f"不支持的传输模式: {transport}")
    except Exception as e:
        tb_text = traceback.format_exc()
        log.log(f"❌ MCP Server 运行时崩溃:\n{tb_text}")
        raise


if __name__ == '__main__':
    import argparse
    import traceback

    # 从配置文件获取默认值
    try:
        config = Config()
        default_transport = config.mcp_transport
        default_host = config.mcp_host
        default_port = config.mcp_port
    except Exception:
        # 配置读取失败时使用硬编码默认值
        default_transport = 'http'
        default_host = '0.0.0.0'
        default_port = 3334

    parser = argparse.ArgumentParser(
        description='Discord Bridge MCP Server - Discord 文件消息发送工具（基于消息队列）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  # 使用 stdio 模式（推荐，用于 Claude Code）
  python server.py

  # 使用 HTTP 模式（用于调试）
  python server.py --transport http --host 0.0.0.0 --port 3334

  # 从配置文件读取（默认行为）
  python server.py
        """
    )
    parser.add_argument(
        '--transport',
        choices=['stdio', 'http'],
        default=default_transport,
        help=f'传输模式：stdio (默认) 或 http（默认从配置文件读取）'
    )
    parser.add_argument(
        '--host',
        default=default_host,
        help=f'HTTP模式的监听地址（默认从配置文件读取，当前: {default_host}）'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=default_port,
        help=f'HTTP模式的监听端口（默认从配置文件读取，当前: {default_port}）'
    )

    args = parser.parse_args()

    try:
        run_server(
            transport=args.transport,
            host=args.host,
            port=args.port
        )
    except Exception as e:
        tb_text = traceback.format_exc()
        log.log(f"❌ MCP Server 启动失败:\n{tb_text}")
        sys.exit(1)
