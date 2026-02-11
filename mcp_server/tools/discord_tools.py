"""
Discord MCP 工具

提供文件发送到 Discord 的 MCP 工具。
"""
from typing import Optional, List
from mcp_server.services.discord_service import (
    get_discord_service,
    FileSendResult,
    MessageSendResult,
    DiscordBridgeError,
    ValidationError,
    FileNotFoundError
)
from shared.message_queue import MessageQueue


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
    """
    try:
        service = get_discord_service()

        # 调用服务发送文件
        result = service.send_files(
            file_paths=[file_path],
            user_id=user_id,
            channel_id=channel_id,
            message=message,
            use_embed=use_embed
        )

        return result.to_json()

    except (ValidationError, FileNotFoundError) as e:
        return FileSendResult(
            success=False,
            message="参数验证失败",
            error=str(e)
        ).to_json()
    except DiscordBridgeError as e:
        return FileSendResult(
            success=False,
            message="文件发送失败",
            error=str(e)
        ).to_json()
    except Exception as e:
        return FileSendResult(
            success=False,
            message="未知错误",
            error=str(e)
        ).to_json()


async def send_multiple_files_to_discord(
    file_paths: List[str],
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    message: Optional[str] = None,
    use_embed: bool = False
) -> str:
    """
    批量发送多个文件到 Discord（支持用户私聊或频道）

    将多个本地文件批量发送到指定 Discord 用户的私聊或频道中。
    Discord 限制单次最多发送 10 个文件。

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
    """
    try:
        service = get_discord_service()

        # 调用服务发送文件
        result = service.send_files(
            file_paths=file_paths,
            user_id=user_id,
            channel_id=channel_id,
            message=message,
            use_embed=use_embed
        )

        return result.to_json()

    except (ValidationError, FileNotFoundError) as e:
        return FileSendResult(
            success=False,
            message="参数验证失败",
            error=str(e)
        ).to_json()
    except DiscordBridgeError as e:
        return FileSendResult(
            success=False,
            message="文件发送失败",
            error=str(e)
        ).to_json()
    except Exception as e:
        return FileSendResult(
            success=False,
            message="未知错误",
            error=str(e)
        ).to_json()


async def list_discord_channels() -> str:
    """
    列出 Bot 可访问的所有频道和服务器

    注意：此工具需要 Discord Bot 正在运行。

    Returns:
        JSON格式的频道列表，包含服务器和频道信息

    Examples:
        list_discord_channels()
    """
    try:
        import json
        from shared.config import Config
        from shared.message_queue import MessageQueue

        # 获取配置
        config = Config()
        message_queue = MessageQueue(config.database_path)

        # 查询当前 Bot 的频道信息（通过数据库）
        # 注意：由于 MCP Server 不直接访问 Discord 客户端，
        # 我们返回一个提示，建议用户使用 Discord Bot 的 /status 命令

        result = {
            "success": True,
            "message": "请使用 Discord Bot 的 /status 命令查看频道信息",
            "data": {
                "note": "MCP Server 通过消息队列与 Bot 通信，无法直接访问 Bot 的频道列表",
                "suggestion": "在 Discord 中发送 /status 命令查看 Bot 可访问的频道和服务器"
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "message": "获取频道列表失败",
            "error": str(e)
        }, ensure_ascii=False, indent=2)


async def send_message_to_discord(
    content: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    use_embed: bool = True,
    embed_title: Optional[str] = None,
    embed_color: Optional[int] = None
) -> str:
    """
    发送纯文本消息到 Discord（使用 Embed 格式）

    将纯文本消息发送到指定 Discord 用户的私聊或频道中。
    默认使用精美的 Embed 卡片格式。

    Args:
        content: 消息内容（必需）
        user_id: Discord 用户 ID（可选），发送到私聊时使用，格式：数字字符串
        channel_id: Discord 频道 ID（可选），发送到频道时使用，格式：数字字符串
        use_embed: 是否使用 Embed 格式发送（默认 True）
            - True: 使用精美卡片格式（推荐）
            - False: 发送纯文本消息
        embed_title: Embed 标题（可选，仅在 use_embed=True 时生效）
        embed_color: Embed 颜色（可选，十进制格式）
                    常用颜色：
                    - 5793266 (蓝色)
                    - 3066993 (绿色)
                    - 16776960 (红色)
                    - 15105570 (黄色)

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 发送纯文本消息到用户私聊
        send_message_to_discord(
            content="你好！这是一条测试消息",
            user_id="123456789"
        )

        # 发送带标题的 Embed 消息到频道
        send_message_to_discord(
            content="这是消息的详细内容",
            channel_id="987654321",
            embed_title="通知标题"
        )

        # 发送带颜色的消息
        send_message_to_discord(
            content="任务已完成！",
            channel_id="987654321",
            embed_title="成功",
            embed_color=3066993  # 绿色
        )

        # 发送纯文本消息（不使用 Embed）
        send_message_to_discord(
            content="简单的纯文本消息",
            user_id="123456789",
            use_embed=False
        )

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - Embed 格式更美观，推荐使用
        - 发送给私聊用户时，user_id 可以从 Discord 开发者模式获取
        - 发送到频道时，channel_id 可以从 Discord 开发者模式获取
        - 此工具通过消息队列与 Discord Bot 通信，需要 Bot 正在运行
    """
    try:
        service = get_discord_service()

        # 调用服务发送消息
        result = service.send_message(
            content=content,
            user_id=user_id,
            channel_id=channel_id,
            use_embed=use_embed,
            embed_title=embed_title,
            embed_color=embed_color
        )

        return result.to_json()
    except ValidationError as e:
        return MessageSendResult(
            success=False,
            message="参数验证失败",
            error=str(e)
        ).to_json()
    except DiscordBridgeError as e:
        return MessageSendResult(
            success=False,
            message="消息发送失败",
            error=str(e)
        ).to_json()
    except Exception as e:
        return MessageSendResult(
            success=False,
            message="未知错误",
            error=str(e)
        ).to_json()

