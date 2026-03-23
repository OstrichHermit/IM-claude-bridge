"""Discord/微信 MCP 工具模块"""

from .discord_tools import (
    _send_file_to_discord,
    _send_multiple_files_to_discord
)
from .weixin_tools import (
    _send_file_to_weixin,
    _send_multiple_files_to_weixin
)

__all__ = [
    '_send_file_to_discord',
    '_send_multiple_files_to_discord',
    '_send_file_to_weixin',
    '_send_multiple_files_to_weixin'
]
