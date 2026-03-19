"""
MCP 时间工具

提供获取当前时间的功能。
"""
from datetime import datetime
import pytz


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
    try:
        # 获取时区
        tz = pytz.timezone(timezone)

        # 获取当前时间
        now = tz.localize(datetime.now())

        # 返回时间信息
        return f'{{"success": true, "timezone": "{timezone}", "datetime": "{now.strftime("%Y-%m-%d %H:%M:%S")}", "timestamp": {int(now.timestamp())}, "date": "{now.strftime("%Y-%m-%d")}", "time": "{now.strftime("%H:%M:%S")}", "unix_timestamp": {int(now.timestamp())}}}'

    except Exception as e:
        # 时区无效或其他错误
        return f'{{"success": false, "error": "无效的时区: {timezone}"}'
