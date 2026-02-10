"""
Discord MCP 工具

提供文件发送到 Discord 的 MCP 工具。
"""
from typing import Optional, List
from mcp_server.services.discord_service import (
    get_discord_service,
    FileSendResult,
    DiscordBridgeError,
    ValidationError,
    FileNotFoundError
)
from shared.message_queue import (
    MessageQueue,
    FileDownloadRequest,
    FileDownloadRequestStatus
)


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


async def download_file_from_discord(
    message_id: str,
    channel_id: str,
    save_directory: str,
    timeout: int = 60
) -> str:
    """
    从 Discord 消息中下载附件到指定目录（支持私聊和频道）

    通过消息队列请求 Discord Bot 下载指定消息的附件。
    Bot 会将附件下载到指定目录，并返回下载结果。

    Args:
        message_id: Discord 消息 ID（必需），格式：数字字符串
        channel_id: Discord 频道/私聊 ID（必需），格式：数字字符串
        save_directory: 本地保存目录路径（必需）
        timeout: 等待超时时间（秒），默认 60 秒

    Returns:
        JSON格式的下载结果，包含成功状态和文件信息

    Examples:
        # 下载频道消息中的附件
        download_file_from_discord(
            message_id="123456789",
            channel_id="987654321",
            save_directory="D:/Downloads"
        )

        # 下载私聊消息中的附件
        download_file_from_discord(
            message_id="123456789",
            channel_id="987654321",
            save_directory="D:/Downloads",
            timeout=120
        )

    Note:
        - message_id 可以从 Discord 开发者模式中复制
        - channel_id 可以从 Discord 开发者模式中复制
        - 支持一条消息多个附件批量下载
        - 自动处理文件名冲突（重命名为 file_1.jpg, file_2.jpg）
        - 自动创建保存目录（如不存在）
        - 此工具通过消息队列与 Discord Bot 通信，需要 Bot 正在运行
    """
    try:
        import json
        from shared.config import Config

        # 获取配置
        config = Config()
        message_queue = MessageQueue(config.database_path)

        # 创建文件下载请求
        download_request = FileDownloadRequest(
            id=None,
            discord_message_id=int(message_id),
            discord_channel_id=int(channel_id),
            save_directory=save_directory,
            status=FileDownloadRequestStatus.PENDING.value
        )

        # 添加到队列
        request_id = message_queue.add_file_download_request(download_request)

        print(f"[文件下载 #{request_id}] 已创建下载请求")
        print(f"[文件下载 #{request_id}] 消息 ID: {message_id}, 频道 ID: {channel_id}")
        print(f"[文件下载 #{request_id}] 保存目录: {save_directory}")

        # 等待 Bot 处理完成
        result = message_queue.get_file_download_request(request_id, timeout=timeout)

        if result is None:
            return json.dumps({
                "success": False,
                "message": f"下载请求超时（{timeout}秒）",
                "error": "timeout",
                "request_id": request_id
            }, ensure_ascii=False, indent=2)

        # 检查结果
        if result.status == FileDownloadRequestStatus.COMPLETED.value:
            # 解析下载的文件列表
            downloaded_files = []
            if result.downloaded_files:
                try:
                    downloaded_files = json.loads(result.downloaded_files)
                except json.JSONDecodeError:
                    pass

            return json.dumps({
                "success": True,
                "message": f"成功下载 {len(downloaded_files)} 个文件到 {save_directory}",
                "request_id": request_id,
                "downloaded_files": downloaded_files,
                "save_directory": save_directory
            }, ensure_ascii=False, indent=2)
        else:
            # 下载失败
            error_msg = result.error or "未知错误"
            return json.dumps({
                "success": False,
                "message": "文件下载失败",
                "error": error_msg,
                "request_id": request_id
            }, ensure_ascii=False, indent=2)

    except ValueError as e:
        return json.dumps({
            "success": False,
            "message": "参数格式错误",
            "error": str(e)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "message": "未知错误",
            "error": str(e)
        }, ensure_ascii=False, indent=2)
