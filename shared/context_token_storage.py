"""
Context Token 持久化存储模块
用于存储微信 context_token，支持进程重启后恢复
直接存储在 weixin_accounts.json 每个账号对象中
"""
import json
from pathlib import Path
from typing import Optional

from shared.logger import get_logger

log = get_logger("ContextTokenStorage", "bridge")


class ContextTokenStorage:
    """Context Token 持久化存储类，直接存储在 weixin_accounts.json 每个账号对象中"""

    def __init__(self, storage_file: str):
        """
        初始化存储

        Args:
            storage_file: weixin_accounts.json 文件路径
        """
        self.storage_file = Path(storage_file)
        self._tokens: dict[str, str] = {}  # username -> token
        self._load()

    def _load(self):
        """从磁盘加载 token"""
        try:
            if self.storage_file.exists():
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 数组格式：每个账号对象直接包含 context_token
                    if isinstance(data, list):
                        for account in data:
                            if isinstance(account, dict):
                                username = account.get('username')
                                token = account.get('context_token')
                                if username and token:
                                    self._tokens[username] = token
                log.log(f"从 {self.storage_file.name} 加载了 {len(self._tokens)} 个 context token")
        except Exception as e:
            log.log(f"⚠️  加载 context token 失败: {e}")
            self._tokens = {}

    def _save(self):
        """保存 token 到磁盘"""
        try:
            data = []
            # 如果文件已存在，先读取（保留其他字段）
            if self.storage_file.exists():
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not isinstance(data, list):
                        # 如果不是数组格式，重置为空数组
                        data = []

            # 更新每个账号对象的 context_token
            for account in data:
                if isinstance(account, dict):
                    username = account.get('username')
                    if username and username in self._tokens:
                        account['context_token'] = self._tokens[username]

            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.log(f"已保存 {len(self._tokens)} 个 context token 到 {self.storage_file.name}")
        except Exception as e:
            log.log(f"⚠️  保存 context token 失败: {e}")

    def get(self, username: str) -> Optional[str]:
        """
        获取用户的 context token

        Args:
            username: 用户名（如"鸵鸟居士"）

        Returns:
            token 字符串，如果不存在则返回 None
        """
        return self._tokens.get(username)

    def set(self, username: str, token: str):
        """
        设置用户的 context token（同时写内存和磁盘）

        Args:
            username: 用户名
            token: context token
        """
        if not username or not token:
            return
        self._tokens[username] = token
        self._save()

    def delete(self, username: str):
        """
        删除用户的 context token

        Args:
            username: 用户名
        """
        if username in self._tokens:
            del self._tokens[username]
            self._save()

    def get_all(self) -> dict[str, str]:
        """
        获取所有 token 的副本

        Returns:
            所有 token 的字典
        """
        return self._tokens.copy()

    def clear(self):
        """清空所有 token"""
        self._tokens = {}
        self._save()
