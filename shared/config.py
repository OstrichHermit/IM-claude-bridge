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

    @property
    def sync_guild_id(self) -> str:
        """获取斜杠命令同步的服务器 ID（留空则全局同步）"""
        return self._config.get('discord', {}).get('sync_guild_id', '')

    @property
    def default_download_directory(self) -> str:
        """获取默认文件下载目录"""
        download_dir = self._config.get('file_download', {}).get('default_directory', './downloads')
        # 转换为绝对路径
        if not os.path.isabs(download_dir):
            project_root = Path(__file__).parent.parent
            download_dir = project_root / download_dir
        return str(download_dir)

    @property
    def allowed_download_directories(self) -> List[str]:
        """获取允许的下载目录列表（空列表 = 允许所有目录）"""
        return self._config.get('file_download', {}).get('allowed_directories', [])

    @property
    def auto_load_enabled(self) -> bool:
        """获取是否启用首次对话提示词注入"""
        return self._config.get('auto_load', {}).get('enabled', False)

    @property
    def auto_load_prompt_text(self) -> str:
        """获取首次对话提示词注入的文本"""
        return self._config.get('auto_load', {}).get('prompt_text', '加载记忆')
