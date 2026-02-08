"""
配置管理模块
"""
import yaml
import os
from pathlib import Path
from typing import Dict, List, Any


class Config:
    """配置管理类"""

    def __init__(self, config_path: str = None):
        """初始化配置"""
        if config_path is None:
            # 默认配置文件路径
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config" / "config.yaml"

        self.config_path = Path(config_path)
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请复制 config.example.yaml 并重命名为 config.yaml"
            )

        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    @property
    def discord_token(self) -> str:
        """获取 Discord Token"""
        token = self._config.get('discord', {}).get('token')
        if not token or token == "YOUR_DISCORD_BOT_TOKEN_HERE":
            raise ValueError("请先在 config.yaml 中设置有效的 Discord Bot Token")
        return token

    @property
    def command_prefix(self) -> str:
        """获取命令前缀"""
        return self._config.get('discord', {}).get('command_prefix', '@')

    @property
    def allowed_channels(self) -> List[int]:
        """获取允许的频道 ID 列表"""
        return self._config.get('discord', {}).get('allowed_channels', [])

    @property
    def allowed_users(self) -> List[int]:
        """获取允许的用户 ID 列表"""
        return self._config.get('discord', {}).get('allowed_users', [])

    @property
    def claude_executable(self) -> str:
        """获取 Claude Code 可执行文件路径"""
        return self._config.get('claude', {}).get('executable', 'claude')

    @property
    def claude_timeout(self) -> int:
        """获取 Claude 超时时间"""
        return self._config.get('claude', {}).get('timeout', 300)

    @property
    def max_retries(self) -> int:
        """获取最大重试次数"""
        return self._config.get('claude', {}).get('max_retries', 3)

    @property
    def working_directory(self) -> str:
        """获取工作目录"""
        wd = self._config.get('claude', {}).get('working_directory', '')
        if not wd:
            # 如果未配置，返回项目根目录
            return str(Path(__file__).parent.parent)
        # 转换为绝对路径
        if not os.path.isabs(wd):
            project_root = Path(__file__).parent.parent
            wd = project_root / wd
        return str(wd)

    @property
    def session_mode(self) -> str:
        """获取会话模式"""
        return self._config.get('claude', {}).get('session_mode', 'none')

    @property
    def database_path(self) -> str:
        """获取消息队列数据库路径"""
        db_path = self._config.get('queue', {}).get('database_path', './shared/messages.db')
        # 转换为绝对路径
        if not os.path.isabs(db_path):
            project_root = Path(__file__).parent.parent
            db_path = project_root / db_path
        return str(db_path)

    @property
    def poll_interval(self) -> int:
        """获取轮询间隔（毫秒）"""
        return self._config.get('queue', {}).get('poll_interval', 500)

    @property
    def message_retention_hours(self) -> int:
        """获取消息保留时间（小时）"""
        return self._config.get('queue', {}).get('message_retention_hours', 24)

    @property
    def startup_notification_channel(self) -> str:
        """获取启动通知频道 ID"""
        return self._config.get('discord', {}).get('startup_notification_channel', '')

    @property
    def startup_notification_user(self) -> str:
        """获取启动通知用户 ID（私聊）"""
        return self._config.get('discord', {}).get('startup_notification_user', '')
