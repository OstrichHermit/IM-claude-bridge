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


# 创建 FastMCP 应用
mcp = FastMCP('discord-bridge')


# ==================== MCP 工具 ====================

@mcp.tool
async def send_file_to_discord(
    file_path: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    message: Optional[str] = None,
    use_embed: bool = False
) -> str:
    """
    发送文件到 Discord（支持用户私聊或频道）

    将本地文件发送到指定 Discord 用户的私聊或频道中。
    通过消息队列与 Discord Bot 通信。

    Args:
        file_path: 要发送的文件路径（必需）
        user_id: Discord 用户 ID（可选），发送到私聊时使用，格式：数字字符串
        channel_id: Discord 频道 ID（可选），发送到频道时使用，格式：数字字符串
        message: 附加文本消息（可选）
        use_embed: 是否使用 Embed 格式发送（默认 False）
            - False: 直接发送文件（简单）
            - True: 使用精美卡片格式（推荐）

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 发送到用户私聊
        send_file_to_discord(file_path="chart.png", user_id="123456789")

        # 发送到频道
        send_file_to_discord(file_path="report.png", channel_id="987654321")

        # 发送 PDF 到用户并附带说明
        send_file_to_discord(
            file_path="report.pdf",
            user_id="123456789",
            message="这是您要的分析报告"
        )

        # 使用 Embed 格式发送到频道
        send_file_to_discord(
            file_path="data.png",
            channel_id="987654321",
            message="数据分析图表",
            use_embed=True
        )

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
        channel_id=channel_id,
        message=message,
        use_embed=use_embed
    )


@mcp.tool
async def send_multiple_files_to_discord(
    file_paths: list,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    message: Optional[str] = None,
    use_embed: bool = False
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
        message: 附加文本消息（可选）
        use_embed: 是否使用 Embed 格式发送（默认 False）
            - False: 直接发送文件（简单）
            - True: 使用精美卡片格式（推荐）

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
            channel_id="987654321",
            message="这是您要的分析报告"
        )

        # 使用 Embed 格式
        send_multiple_files_to_discord(
            file_paths=["image1.png", "image2.png"],
            user_id="123456789",
            message="图片合集",
            use_embed=True
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
        channel_id=channel_id,
        message=message,
        use_embed=use_embed
    )


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
    print()
    print("  架构说明:")
    print("    - MCP Server 通过消息队列与 Discord Bot 通信")
    print("    - 无需创建新的 Discord 客户端")
    print("    - 需要确保 Discord Bot 正在运行")
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
