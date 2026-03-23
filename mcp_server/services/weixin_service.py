"""
微信服务层

提供文件发送到微信的服务实现。
"""
import os
import zlib
import yaml
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict
from shared.message_queue import MessageQueue
from shared.config import Config


class WeixinBridgeError(Exception):
    """微信桥接错误基类"""
    pass


class ValidationError(WeixinBridgeError):
    """参数验证错误"""
    pass


class FileNotFoundError(WeixinBridgeError):
    """文件未找到错误"""
    pass


@dataclass
class FileSendResult:
    """文件发送结果"""
    success: bool
    message: str
    sent_count: int = 0
    failed_files: List[str] = None
    error: str = None

    def __post_init__(self):
        if self.failed_files is None:
            self.failed_files = []

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


import json


class WeixinService:
    """微信服务类"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.config = Config()
        self.message_queue = MessageQueue(self.config.database_path)
        self.user_mapping = {}  # 先初始化为空字典
        self._initialized = True

        # 加载微信账号配置（用于用户名映射）
        self._load_user_mapping()

    def ensure_mapping_loaded(self):
        """确保用户名映射已加载"""
        if not self.user_mapping:
            print("🔄 重新加载用户名映射...")
            self._load_user_mapping()

    def _load_user_mapping(self):
        """从账号配置中加载用户信息"""
        self.user_mapping = {}  # wxid -> username
        self.username_to_wxid = {}  # username -> wxid
        self.userid_to_user = {}  # user_id -> {wxid, username, user_id}

        try:
            import json

            with open(self.config.weixin_accounts_file, 'r', encoding='utf-8') as f:
                accounts = json.load(f)

            for acc in accounts:
                wxid = acc["wxid"]
                username = acc["username"]
                user_id = acc["user_id"]

                self.user_mapping[wxid] = username
                self.username_to_wxid[username] = wxid
                self.userid_to_user[user_id] = {
                    "wxid": wxid,
                    "username": username,
                    "user_id": user_id
                }

            print(f"✅ 加载了 {len(accounts)} 个用户信息")
            if accounts:
                print(f"📋 用户示例: {[(acc['username'], acc['user_id']) for acc in accounts[:3]]}")
        except FileNotFoundError:
            print(f"⚠️  账号文件不存在: {self.config.weixin_accounts_file}")
        except Exception as e:
            print(f"⚠️  加载用户信息失败: {e}")
            import traceback
            traceback.print_exc()

    def weixin_id_to_int(self, weixin_id: str) -> int:
        """将微信用户ID（wxid）转换为固定的整数ID

        从配置中读取预先计算好的 user_id，不再动态转换
        """
        self.ensure_mapping_loaded()

        # 从 username_to_wxid 反向查找 wxid 对应的用户信息
        for user_id, user_info in self.userid_to_user.items():
            if user_info["wxid"] == weixin_id:
                print(f"🔄 ID查找: {weixin_id} -> {user_id}")
                return user_id

        # 如果找不到，返回 0
        print(f"⚠️  未找到用户: {weixin_id}")
        return 0

    def send_files(
        self,
        file_paths: List[str],
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None
    ) -> FileSendResult:
        """
        发送文件到微信

        Args:
            file_paths: 文件路径列表
            user_id: 微信用户 ID（私聊）
            channel_id: 微信群聊 ID

        Returns:
            FileSendResult: 发送结果
        """
        # 确保用户名映射已加载
        self.ensure_mapping_loaded()

        # 参数验证
        if not file_paths:
            raise ValidationError("文件路径列表不能为空")

        if user_id is None and channel_id is None:
            raise ValidationError("必须指定 user_id 或 channel_id 其中之一")

        # 验证文件存在
        valid_files = []
        failed_files = []

        for file_path in file_paths:
            if not os.path.exists(file_path):
                failed_files.append(file_path)
                print(f"⚠️  文件不存在: {file_path}")
            else:
                valid_files.append(file_path)

        if not valid_files:
            raise FileNotFoundError("所有文件都不存在")

        # 发送文件请求到消息队列
        # 注意：这里使用和 Discord 相同的 FileRequest 表
        # 因为微信和 Discord 共享同一个消息队列系统
        from shared.message_queue import FileRequest, FileRequestStatus

        request = FileRequest(
            id=None,
            file_paths=json.dumps(valid_files),
            user_id=self.weixin_id_to_int(user_id) if user_id else None,
            channel_id=self.weixin_id_to_int(channel_id) if channel_id else None,
            channel_type="weixin",  # 标记为微信文件请求
            status=FileRequestStatus.PENDING.value
        )

        print(f"📋 文件请求: 原始 user_id={user_id}")
        if user_id:
            mapped_id = self.user_mapping.get(user_id, user_id)
            print(f"📋 映射后: {user_id} -> {mapped_id}")
            int_id = self.weixin_id_to_int(user_id)
            print(f"📋 转整数: {mapped_id} -> {int_id}")
        else:
            print(f"📋 user_id 为空，使用 channel_id")

        req_id = self.message_queue.add_file_request(request)
        print(f"✅ 文件发送请求已加入队列 (ID: {req_id})")

        return FileSendResult(
            success=True,
            message=f"已将 {len(valid_files)} 个文件加入发送队列",
            sent_count=len(valid_files),
            failed_files=failed_files
        )


# 全局服务实例
_weixin_service: Optional[WeixinService] = None


def get_weixin_service() -> WeixinService:
    """获取微信服务实例（单例）"""
    global _weixin_service
    if _weixin_service is None:
        _weixin_service = WeixinService()
    return _weixin_service
