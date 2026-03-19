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
import sys
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.tools import (
    _send_file_to_discord,
    _send_multiple_files_to_discord
)
from mcp_server.tools.scheduler import (
    add_cron as _add_cron_impl,
    list_cron as _list_cron_impl,
    delete_cron as _delete_cron_impl,
    toggle_cron as _toggle_cron_impl,
    get_cron_info as _get_cron_info_impl
)
from mcp_server.tools.time import (
    get_current_time as _get_current_time_impl
)


# 创建 FastMCP 应用
mcp = FastMCP('discord-bridge')


# ==================== MCP 工具 ====================

@mcp.tool
async def send_file_to_discord(
    file_path: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None
) -> str:
    """
    发送文件到 Discord（支持用户私聊或频道）

    将本地文件发送到指定 Discord 用户的私聊或频道中。
    通过消息队列与 Discord Bot 通信。

    Args:
        file_path: 要发送的文件路径（必需）
        user_id: Discord 用户 ID（可选），发送到私聊时使用，格式：数字字符串
        channel_id: Discord 频道 ID（可选），发送到频道时使用，格式：数字字符串

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 发送到用户私聊
        send_file_to_discord(file_path="chart.png", user_id="123456789")

        # 发送到频道
        send_file_to_discord(file_path="report.png", channel_id="987654321")

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - 文件大小限制：普通服务器 25MB，Nitro 500MB
        - 支持格式：图片、PDF、文本、压缩包等所有 Discord 支持的格式
        - 发送给私聊用户时，user_id 可以从 Discord 开发者模式获取
        - 发送到频道时，channel_id 可以从 Discord 开发者模式获取
        - 此工具通过消息队列与 Discord Bot 通信，需要 Bot 正在运行
    """
    return await _send_file_to_discord(
        file_path=file_path,
        user_id=user_id,
        channel_id=channel_id
    )


@mcp.tool
async def send_multiple_files_to_discord(
    file_paths: list,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None
) -> str:
    """
    批量发送多个文件到 Discord（支持用户私聊或频道）

    将多个本地文件批量发送到指定 Discord 用户的私聊或频道中。
    Discord 限制单次最多发送 10 个文件。
    通过消息队列与 Discord Bot 通信。

    Args:
        file_paths: 要发送的文件路径列表（必需），最多 10 个文件
        user_id: Discord 用户 ID（可选），发送到私聊时使用，格式：数字字符串
        channel_id: Discord 频道 ID（可选），发送到频道时使用，格式：数字字符串

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 批量发送图片到用户私聊
        send_multiple_files_to_discord(
            file_paths=["chart1.png", "chart2.png", "data.pdf"],
            user_id="123456789"
        )

        # 批量发送文件到频道
        send_multiple_files_to_discord(
            file_paths=["report1.pdf", "report2.pdf"],
            channel_id="987654321"
        )

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - 最多支持 10 个文件（Discord 限制）
        - 文件大小限制：普通服务器 25MB，Nitro 500MB（每个文件）
        - 如果某些文件不存在或发送失败，会跳过这些文件继续发送其他文件
        - 此工具通过消息队列与 Discord Bot 通信，需要 Bot 正在运行
    """
    return await _send_multiple_files_to_discord(
        file_paths=file_paths,
        user_id=user_id,
        channel_id=channel_id
    )


@mcp.tool
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
    return await _add_cron_impl(
        cron_expr=cron_expr,
        content=content,
        username=username,
        user_id=user_id,
        channel_id=channel_id,
        tag=tag,
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
    transport: str = 'stdio',
    host: str = '0.0.0.0',
    port: int = 3334
):
    """
    启动 MCP 服务器

    Args:
        transport: 传输模式，'stdio' 或 'http'
        host: HTTP模式的监听地址，默认 0.0.0.0
        port: HTTP模式的监听端口，默认 3334（避免与 TrendRadar 冲突）
    """
    print()
    print("=" * 60)
    print("  Discord Bridge MCP Server (基于消息队列)")
    print("=" * 60)
    print(f"  传输模式: {transport.upper()}")

    if transport == 'stdio':
        print("  协议: MCP over stdio (标准输入输出)")
    elif transport == 'http':
        print(f"  协议: MCP over HTTP")
        print(f"  服务器监听: {host}:{port}")

    print()
    print("  已注册的工具:")
    print("    1. send_file_to_discord          - 发送文件到 Discord（支持私聊/频道）")
    print("    2. send_multiple_files_to_discord - 批量发送文件到 Discord（最多10个，支持私聊/频道）")
    print("    3. add_cron                     - 添加定时任务")
    print("    4. list_cron                    - 列出所有定时任务")
    print("    5. delete_cron                  - 删除定时任务")
    print("    6. toggle_cron                  - 启用/禁用定时任务")
    print("    7. get_cron_info                - 获取定时任务详情")
    print("    8. get_current_time             - 获取当前时间（支持多时区）")
    print()
    print("  架构说明:")
    print("    - MCP Server 通过消息队列与 Discord Bot 通信")
    print("    - 无需创建新的 Discord 客户端")
    print("    - 需要确保 Discord Bot 正在运行")
    print("    - 定时任务由 Discord Bot 调度执行")
    print("=" * 60)
    print()

    # 根据传输模式运行服务器
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


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Discord Bridge MCP Server - Discord 文件消息发送工具（基于消息队列）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用 stdio 模式（推荐，用于 Claude Code）
  python server.py

  # 使用 HTTP 模式（用于调试）
  python server.py --transport http --host 0.0.0.0 --port 3334
        """
    )
    parser.add_argument(
        '--transport',
        choices=['stdio', 'http'],
        default='stdio',
        help='传输模式：stdio (默认) 或 http'
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='HTTP模式的监听地址，默认 0.0.0.0'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=3334,
        help='HTTP模式的监听端口，默认 3334'
    )

    args = parser.parse_args()

    run_server(
        transport=args.transport,
        host=args.host,
        port=args.port
    )
