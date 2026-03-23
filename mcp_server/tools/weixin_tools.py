"""
微信 MCP 工具

提供文件发送到微信的 MCP 工具。
"""
from typing import Optional, List
from mcp_server.services.weixin_service import (
    get_weixin_service,
    FileSendResult,
    WeixinBridgeError,
    ValidationError,
    FileNotFoundError
)
from shared.message_queue import MessageQueue


async def _send_file_to_weixin(
    file_path: str,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None
) -> str:
    """
    发送文件到微信（支持用户私聊或群聊）

    将本地文件发送到指定微信用户的私聊或群聊中。

    Args:
        file_path: 要发送的文件路径（必需）
        user_id: 微信用户 ID（可选），发送到私聊时使用
        channel_id: 微信群聊 ID（可选），发送到群聊时使用

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 发送到用户私聊
        send_file_to_weixin(file_path="chart.png", user_id="wxid_xxx")

        # 发送到群聊
        send_file_to_weixin(file_path="report.png", channel_id="chatroom_xxx")

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - 文件大小限制：微信限制 100MB
        - 支持格式：图片、PDF、文本、压缩包等所有微信支持的格式
        - 此工具通过消息队列与微信 Bot 通信，需要 Bot 正在运行
    """
    try:
        service = get_weixin_service()

        # 调用服务发送文件
        result = service.send_files(
            file_paths=[file_path],
            user_id=user_id,
            channel_id=channel_id
        )

        return result.to_json()

    except (ValidationError, FileNotFoundError) as e:
        return FileSendResult(
            success=False,
            message="参数验证失败",
            error=str(e)
        ).to_json()
    except WeixinBridgeError as e:
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


async def _send_multiple_files_to_weixin(
    file_paths: List[str],
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None
) -> str:
    """
    批量发送多个文件到微信（支持用户私聊或群聊）

    将多个本地文件批量发送到指定微信用户的私聊或群聊中。
    微信限制单次最多发送 9 个文件。

    Args:
        file_paths: 要发送的文件路径列表（必需），最多 9 个文件
        user_id: 微信用户 ID（可选），发送到私聊时使用
        channel_id: 微信群聊 ID（可选），发送到群聊时使用

    Returns:
        JSON格式的发送结果，包含成功状态和消息信息

    Examples:
        # 批量发送图片到用户私聊
        send_multiple_files_to_weixin(
            file_paths=["chart1.png", "chart2.png", "data.pdf"],
            user_id="wxid_xxx"
        )

        # 批量发送文件到群聊
        send_multiple_files_to_weixin(
            file_paths=["report1.pdf", "report2.pdf"],
            channel_id="chatroom_xxx"
        )

    Note:
        - user_id 和 channel_id 必须指定其中一个
        - 最多支持 9 个文件（微信限制）
        - 文件大小限制：微信限制 100MB（每个文件）
        - 如果某些文件不存在或发送失败，会跳过这些文件继续发送其他文件
    """
    try:
        service = get_weixin_service()

        # 调用服务发送文件
        result = service.send_files(
            file_paths=file_paths,
            user_id=user_id,
            channel_id=channel_id
        )

        return result.to_json()

    except (ValidationError, FileNotFoundError) as e:
        return FileSendResult(
            success=False,
            message="参数验证失败",
            error=str(e)
        ).to_json()
    except WeixinBridgeError as e:
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
