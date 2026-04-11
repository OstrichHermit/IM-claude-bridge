"""
插入外部消息到 Discord Bridge（模拟从外部发送的消息）
"""
import sys
import time
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType


def insert_external_message(
    content: str,
    username: str = "测试用户",
    user_id: int = 123456789,
    channel_id: int = 987654321,
    is_dm: bool = False,
    use_message_request: bool = False,  # 默认使用 messages（方式2）
    tag: str = MessageTag.DEFAULT.value,  # 消息标签
    channel_type: str = ChannelType.DISCORD.value,  # 频道类型（默认 Discord）
    db_path: str = None
) -> int:
    """
    插入外部消息到 Discord Bridge（模拟从外部发送的消息）

    Args:
        content: 消息内容
        username: 用户名
        user_id: Discord 用户 ID
        channel_id: Discord 频道/私聊 ID
        is_dm: 是否为私聊消息
        use_message_request: 是否使用 message_requests 表（推荐：True）
        tag: 消息标签（默认：default）
        db_path: 数据库路径（默认使用项目中的数据库）

    Returns:
        消息 ID
    """
    # 默认数据库路径（从配置文件读取）
    if db_path is None:
        import yaml
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                db_path = str(Path(__file__).parent.parent / config['queue']['database_path'])
        else:
            # 降级：使用默认路径
            db_path = str(Path(__file__).parent.parent / "shared" / "messages.db")

    # 创建消息队列实例
    queue = MessageQueue(db_path)

    if use_message_request:
        # 使用 message_requests 表（推荐：直接发送到 Discord）
        from shared.message_queue import MessageRequest
        message_request = MessageRequest(
            content=content,
            user_id=user_id if is_dm else None,
            channel_id=None if is_dm else channel_id,
            use_embed=True,
            embed_title=f"来自 {username} 的消息",
            embed_color=3447003,  # Discord 蓝色
            tag=tag
        )
        message_id = queue.add_message_request(message_request)
        return message_id
    else:
        # 使用 messages 表（模拟对话流程）
        message = Message(
            id=None,
            direction=MessageDirection.TO_CLAUDE.value,
            content=content,
            status=MessageStatus.PENDING.value,
            discord_channel_id=channel_id,
            discord_message_id=int(time.time() * 1000),
            discord_user_id=user_id,
            username=username,
            is_dm=is_dm,
            is_external=True,  # 标记为外部消息
            tag=tag,
            channel_type=channel_type  # 频道类型
        )
        message_id = queue.add_message(message)
        return message_id


def main():
    parser = argparse.ArgumentParser(
        description="插入外部消息到 Discord Bridge（模拟从外部发送的消息）"
    )

    parser.add_argument(
        "content",
        help="消息内容"
    )

    parser.add_argument(
        "--username", "-u",
        default="测试用户",
        help="用户名（默认：测试用户）"
    )

    parser.add_argument(
        "--user-id", "-i",
        type=int,
        default=123456789,
        help="Discord 用户 ID（默认：123456789）"
    )

    parser.add_argument(
        "--channel-id", "-c",
        type=int,
        default=987654321,
        help="Discord 频道/私聊 ID（默认：987654321）"
    )

    parser.add_argument(
        "--is-dm",
        action="store_true",
        help="是否为私聊消息"
    )

    parser.add_argument(
        "--use-message-request",
        action="store_true",
        help="使用 message_requests 表（默认使用 messages 表）"
    )

    parser.add_argument(
        "--tag", "-t",
        default=MessageTag.DEFAULT.value,
        choices=[tag.value for tag in MessageTag],
        help=f"消息标签（默认：{MessageTag.DEFAULT.value}）"
    )

    parser.add_argument(
        "--channel-type", "-ct",
        default=ChannelType.DISCORD.value,
        choices=[ct.value for ct in ChannelType],
        help=f"频道类型（默认：{ChannelType.DISCORD.value}）"
    )

    parser.add_argument(
        "--db-path",
        help="自定义数据库路径（默认：shared/messages.db）"
    )

    args = parser.parse_args()

    # 发送消息
    use_mr = args.use_message_request
    print(f"📤 正在插入外部消息...")
    print(f"   内容: {args.content}")
    print(f"   用户: {args.username} (ID: {args.user_id})")
    print(f"   频道: {args.channel_id}")
    print(f"   类型: {'私聊' if args.is_dm else '频道'}")
    print(f"   标签: {args.tag}")
    print(f"   频道类型: {args.channel_type}")
    print(f"   方式: {'message_requests (直接发送)' if use_mr else 'messages (对话流程)'}")
    print()

    try:
        message_id = insert_external_message(
            content=args.content,
            username=args.username,
            user_id=args.user_id,
            channel_id=args.channel_id,
            is_dm=args.is_dm,
            use_message_request=use_mr,
            tag=args.tag,
            channel_type=args.channel_type,
            db_path=args.db_path
        )

        print(f"✅ 外部消息已成功插入！")
        print(f"   消息 ID: {message_id}")
        print()
        print(f"💡 提示:")
        print(f"   - 如果 Claude Bridge 正在运行，消息将被自动处理")
        print(f"   - 可以在数据库中查看消息状态和处理结果")

    except Exception as e:
        print(f"❌ 插入失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
