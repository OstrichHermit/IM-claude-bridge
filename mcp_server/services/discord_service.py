"""
Discord 文件发送服务

通过消息队列与 Discord Bot 通信，实现文件发送功能。
"""
import json
import os
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

import sys
# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.message_queue import MessageQueue, FileRequest, FileRequestStatus, MessageRequest, MessageRequestStatus
from shared.config import Config


@dataclass
class FileSendResult:
    """文件发送结果"""
    success: bool
    message: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    file_count: int = 0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "success": self.success,
            "message": self.message,
            "message_id": self.message_id,
            "error": self.error,
            "file_count": self.file_count
        }

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class DiscordBridgeError(Exception):
    """Discord Bridge 错误基类"""
    pass


class ValidationError(DiscordBridgeError):
    """参数验证错误"""
    pass


class FileNotFoundError(DiscordBridgeError):
    """文件不存在错误"""
    pass


class DiscordService:
    """Discord 文件发送服务"""

    def __init__(self, project_root: Optional[Path] = None):
        """初始化服务

        Args:
            project_root: 项目根目录，默认为当前文件的上上级目录
        """
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent

        # 加载配置
        config_path = project_root / 'config' / 'config.yaml'
        self.config = Config(str(config_path))

        # 初始化消息队列
        self.message_queue = MessageQueue(self.config.database_path)

    def validate_file_paths(self, file_paths: List[str]) -> List[str]:
        """验证文件路径

        Args:
            file_paths: 文件路径列表

        Returns:
            有效的文件路径列表

        Raises:
            ValidationError: 文件数量超过限制
            FileNotFoundError: 文件不存在
        """
        if len(file_paths) > 10:
            raise ValidationError(f"最多支持发送 10 个文件，当前提供了 {len(file_paths)} 个")

        valid_files = []
        invalid_files = []

        for file_path in file_paths:
            if os.path.exists(file_path):
                valid_files.append(file_path)
            else:
                invalid_files.append(file_path)

        if not valid_files:
            raise FileNotFoundError(f"没有有效的文件，无效文件: {invalid_files}")

        return valid_files

    def validate_target(self, user_id: Optional[str] = None,
                       channel_id: Optional[str] = None) -> tuple:
        """验证发送目标

        Args:
            user_id: Discord 用户 ID
            channel_id: Discord 频道 ID

        Returns:
            (user_id_int, channel_id_int) 元组

        Raises:
            ValidationError: 目标参数无效
        """
        if not user_id and not channel_id:
            raise ValidationError("必须指定 user_id 或 channel_id 中的一个")

        if user_id and channel_id:
            raise ValidationError("不能同时指定 user_id 和 channel_id")

        # 转换为整数
        try:
            user_id_int = int(user_id) if user_id else None
            channel_id_int = int(channel_id) if channel_id else None
        except ValueError:
            raise ValidationError("user_id 和 channel_id 必须是数字字符串")

        return user_id_int, channel_id_int

    def send_files(self, file_paths: List[str],
                   user_id: Optional[str] = None,
                   channel_id: Optional[str] = None,
                   use_embed: bool = False,
                   timeout: float = 30.0) -> FileSendResult:
        """发送文件到 Discord

        Args:
            file_paths: 文件路径列表
            user_id: Discord 用户 ID
            channel_id: Discord 频道 ID
            use_embed: 是否使用 Embed 格式
            timeout: 等待超时时间（秒）

        Returns:
            文件发送结果

        Raises:
            ValidationError: 参数验证失败
            FileNotFoundError: 文件不存在
        """
        try:
            # 验证文件路径
            valid_files = self.validate_file_paths(file_paths)

            # 验证发送目标
            user_id_int, channel_id_int = self.validate_target(user_id, channel_id)

            # 创建文件请求
            file_request = FileRequest(
                id=None,
                file_paths=valid_files,
                user_id=user_id_int,
                channel_id=channel_id_int,
                use_embed=use_embed,
                status=FileRequestStatus.PENDING.value,
                result=None,
                error=None
            )

            # 添加到队列
            request_id = self.message_queue.add_file_request(file_request)
            print(f"📁 文件请求已创建: #{request_id}")

            # 等待处理完成
            completed_request = self.message_queue.get_file_request(request_id, timeout=timeout)

            if completed_request is None:
                return FileSendResult(
                    success=False,
                    message="文件发送超时",
                    error=f"等待 {timeout} 秒后仍未完成"
                )

            # 解析结果
            if completed_request.status == FileRequestStatus.COMPLETED.value:
                result_data = json.loads(completed_request.result) if completed_request.result else {}
                return FileSendResult(
                    success=True,
                    message=result_data.get("message", "文件发送成功"),
                    message_id=result_data.get("message_id"),
                    file_count=len(valid_files)
                )
            else:
                error_data = json.loads(completed_request.error) if completed_request.error else {}
                return FileSendResult(
                    success=False,
                    message="文件发送失败",
                    error=error_data.get("error", "未知错误")
                )

        except (ValidationError, FileNotFoundError) as e:
            # 重新抛出验证错误
            raise
        except Exception as e:
            # 包装其他异常
            raise DiscordBridgeError(f"文件发送时出错: {str(e)}")


# 单例模式
_service_instance: Optional[DiscordService] = None


def get_discord_service() -> DiscordService:
    """获取 Discord 服务单例

    Returns:
        Discord 服务实例
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = DiscordService()
    return _service_instance
