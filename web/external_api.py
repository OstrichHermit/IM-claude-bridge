"""
外部消息注入模块

供外部程序（如炉石监听工具 hs watch）通过 Web API 将消息注入到
指定 Discord 频道的正常会话流程：构造 is_external=True 的 TO_CLAUDE 消息
写入消息队列，由 Claude Bridge 调度到该频道的常驻 Worker
（session_key = channel_{频道ID}），AI 响应会通过 Discord Bot 发回频道。
"""
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.config import Config
from shared.logger import get_logger
from shared.message_queue import (
    MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType
)

log = get_logger("ExternalAPI", "manager")


def get_db_path() -> str:
    """获取消息队列数据库路径（独立函数，便于测试时替换到临时目录）"""
    return Config().database_path


def inject_external_message(channel_id: int, content: str, source: str,
                            db_path: Optional[str] = None) -> Tuple[int, str]:
    """注入外部消息到指定 Discord 频道的会话流程

    字段构造方式与 scripts/insert_external_message.py 保持一致：
    direction=TO_CLAUDE、is_external=True、channel_type=discord。

    注意：tag 使用来源原值（如 hs-watch）。tag 不能是 task/reminder，
    否则 session 会被路由到临时会话（temp_{id}）而不是目标频道；
    调用方（Web API 端点）需先用 allowed_sources 校验来源。

    Args:
        channel_id: Discord 频道 ID
        content: 消息内容（作为提示词发给 Claude）
        source: 来源标识（如 hs-watch），写入 tag 列并在 username 中体现
        db_path: 消息队列数据库路径（默认从配置读取）

    Returns:
        (message_id, session_key) 元组
    """
    if db_path is None:
        db_path = get_db_path()

    tag = source if source else MessageTag.DEFAULT.value

    queue = MessageQueue(db_path)
    message = Message(
        id=None,
        direction=MessageDirection.TO_CLAUDE.value,
        content=content,
        status=MessageStatus.PENDING.value,
        discord_channel_id=channel_id,
        discord_message_id=int(time.time() * 1000),
        discord_user_id=0,
        username=source if source else "external",
        is_dm=False,
        is_external=True,  # 标记为外部消息
        tag=tag,
        channel_type=ChannelType.DISCORD.value
    )
    message_id = queue.add_message(message)
    session_key = queue._calculate_session_key(message)
    return message_id, session_key
