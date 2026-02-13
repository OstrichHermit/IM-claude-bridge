"""
触发定时任务 - 向 Claude Bridge 发送定时消息

⚠️ 重要提示：
- 当通过 MCP Scheduler 调用时，content 参数只支持英文（ASCII 字符）
- 如需发送中文内容，请创建专用批处理文件，内容硬编码中文
"""
import argparse
from pathlib import Path

# 添加 shared 目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent))

from insert_external_message import insert_external_message


def trigger_scheduled_task(
    content: str,
    user_id: int = None,
    channel_id: int = None,
    is_dm: bool = False
) -> int:
    """
    触发定时任务，向 Claude Bridge 发送定时消息

    ⚠️ content 参数限制：
    - MCP Scheduler 调用时：仅支持英文（ASCII）
    - 直接调用时：支持中文

    Args:
        content: 消息内容（MCP 调用请用英文）
        user_id: Discord 用户 ID（私聊模式必须提供）
        channel_id: Discord 频道 ID（频道模式必须提供）
        is_dm: 是否为私聊消息（默认：False）

    Returns:
        消息 ID

    Raises:
        ValueError: 参数不合法时抛出
    """
    # 参数校验
    if is_dm:
        if user_id is None:
            raise ValueError("私聊模式必须提供 user_id 参数")
        channel_id = user_id  # Discord 私聊的 channel_id = user_id
    else:
        if channel_id is None:
            raise ValueError("频道模式必须提供 channel_id 参数")
        if user_id is None:
            user_id = 0  # 频道消息不需要 user_id

    # 固定配置：使用 messages 表，方向为 TO_CLAUDE，db_path 为默认
    message_id = insert_external_message(
        content=content,
        username="OH-Bot 的定时任务已触发",  # 固定值
        user_id=user_id,
        channel_id=channel_id,
        is_dm=is_dm,
        use_message_request=False,  # 固定使用 messages 表
        db_path=None               # 固定使用默认数据库路径
    )
    return message_id


def main():
    parser = argparse.ArgumentParser(
        description="触发定时任务 - 向 Claude Bridge 发送定时消息（用户名：OH-Bot，方向：TO_CLAUDE，表：messages）\n\n⚠️ MCP 调用提示：content 请使用英文（ASCII 字符）"
    )

    parser.add_argument(
        "content",
        nargs='?',  # 变为可选参数
        help="消息内容（MCP 调用请用英文，或使用 --config-file 从文件读取）"
    )

    parser.add_argument(
        "--config-file", "-f",
        help="从配置文件读取消息内容（支持 UTF-8 中文）"
    )

    parser.add_argument(
        "--user-id", "-i",
        type=int,
        default=None,
        help="Discord 用户 ID（私聊模式必须提供）"
    )

    parser.add_argument(
        "--channel-id", "-c",
        type=int,
        default=None,
        help="Discord 频道 ID（频道模式必须提供）"
    )

    parser.add_argument(
        "--is-dm",
        action="store_true",
        help="是否为私聊消息（提供 --user-id 时自动启用）"
    )

    args = parser.parse_args()

    # 确定消息内容：从文件读取或直接使用
    if args.config_file:
        with open(args.config_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        print(f"📄 从配置文件读取消息: {args.config_file}")
    elif args.content:
        content = args.content
    else:
        parser.error("必须提供 content 或 --config-file 参数")

    # 智能判断模式：提供了 --user-id 就是私聊模式
    is_dm_mode = args.is_dm or args.user_id is not None
    target_user_id = args.user_id if is_dm_mode else 0
    target_channel_id = args.user_id if is_dm_mode else args.channel_id

    # 触发定时任务
    print(f"⏰ 正在触发定时任务...")
    print(f"   内容: {content}")
    print(f"   类型: {'私聊（DM）' if is_dm_mode else '频道'}")
    if is_dm_mode:
        print(f"   目标: 私聊 {args.user_id}")
    else:
        print(f"   目标: 频道 {args.channel_id}")
    print(f"   方向: TO_CLAUDE（固定）")
    print(f"   表: messages（固定）")
    print(f"   数据库: 默认路径（固定）")
    print()

    try:
        message_id = trigger_scheduled_task(
            content=content,
            user_id=target_user_id,
            channel_id=target_channel_id,
            is_dm=is_dm_mode
        )

        print(f"✅ 定时任务已成功触发！")
        print(f"   消息 ID: {message_id}")
        print()
        print(f"💡 提示:")
        print(f"   - 如果 Claude Bridge 正在运行，消息将被自动处理")
        print(f"   - 可以在数据库的 messages 表中查看消息状态")

    except Exception as e:
        print(f"❌ 触发失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
