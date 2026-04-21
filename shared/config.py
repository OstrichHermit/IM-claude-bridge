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
    def max_attempts(self) -> int:
        """获取 Claude Code 最大调用尝试次数（包括第一次调用）"""
        return self._config.get('claude', {}).get('max_attempts', 3)

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
    def stickers_path(self) -> str:
        """获取表情包目录路径"""
        stickers_dir = self._config.get('discord', {}).get('stickers_path', './stickers')
        # 转换为绝对路径
        if not os.path.isabs(stickers_dir):
            project_root = Path(__file__).parent.parent
            stickers_dir = project_root / stickers_dir
        return str(stickers_dir)

    @property
    def mention_required(self) -> bool:
        """获取是否需要 @机器人 才能触发对话"""
        return self._config.get('discord', {}).get('mention_required', True)

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
    def auto_load_enabled(self) -> bool:
        """获取是否启用首次对话提示词注入"""
        return self._config.get('auto_load', {}).get('enabled', False)

    @property
    def auto_load_prompt_text(self) -> str:
        """获取首次对话提示词注入的文本"""
        return self._config.get('auto_load', {}).get('prompt_text', '加载记忆')

    @property
    def auto_trigger_before_new_enabled(self) -> bool:
        """获取是否启用 /new 前自动发送提示词"""
        return self._config.get('auto_trigger_before_new', {}).get('enabled', False)

    @property
    def auto_trigger_before_new_message(self) -> str:
        """获取 /new 前自动发送的预设用户消息内容"""
        return self._config.get('auto_trigger_before_new', {}).get('prompt_text', '')

    @property
    def auto_trigger_after_new_enabled(self) -> bool:
        """获取是否启用 /new 后自动触发对话"""
        return self._config.get('auto_trigger_after_new', {}).get('enabled', False)

    @property
    def auto_trigger_after_new_message(self) -> str:
        """获取 /new 后自动触发的预设用户消息内容"""
        return self._config.get('auto_trigger_after_new', {}).get('prompt_text', '')

    # Typing indicator 配置

    @property
    def typing_indicator_max_retries(self) -> int:
        """获取 typing indicator 最大连续重试次数"""
        return self._config.get('typing_indicator', {}).get('max_retries', 3)

    @property
    def typing_indicator_retry_delay(self) -> int:
        """获取 typing indicator 重试等待时间（秒）"""
        return self._config.get('typing_indicator', {}).get('retry_delay', 5)

    # 超时配置

    @property
    def timeout_pending(self) -> int:
        """获取 PENDING 状态超时时间（秒）"""
        return self._config.get('timeout', {}).get('pending', 30)

    @property
    def max_concurrent_sessions(self) -> int:
        """获取最大并发 session 数（0 = 无限制）"""
        return self._config.get('claude', {}).get('max_concurrent_sessions', 5)

    @property
    def worker_idle_timeout(self) -> int:
        """获取 Worker 空闲超时时间（秒，0 = 永不清理）"""
        return self._config.get('claude', {}).get('worker_idle_timeout', 300)

    @property
    def tool_use_notification_enabled(self) -> bool:
        """获取是否启用工具调用通知"""
        return self._config.get('tool_use_notification', {}).get('enabled', False)

    @property
    def tool_emoji_mapping(self) -> Dict[str, str]:
        """获取工具 emoji 映射配置"""
        return self._config.get('tool_use_notification', {}).get('emoji_mapping', {})

    @property
    def cron_enabled(self) -> bool:
        """获取是否启用定时任务调度器"""
        return self._config.get('cron_scheduler', {}).get('enabled', True)

    @property
    def cron_storage_path(self) -> str:
        """获取定时任务存储路径"""
        storage_path = self._config.get('cron_scheduler', {}).get('storage_path', './shared/cron_jobs.json')
        # 转换为绝对路径
        if not os.path.isabs(storage_path):
            project_root = Path(__file__).parent.parent
            storage_path = project_root / storage_path
        return str(storage_path)

    # 消息队列配置

    @property
    def queue_send_interval(self) -> float:
        """获取消息队列的发送间隔（秒）"""
        return self._config.get('queue', {}).get('send_interval', 1.5)

    # 消息分割配置

    @property
    def enable_message_splitting(self) -> bool:
        """获取是否启用消息按空行分割功能"""
        return self._config.get('message_splitting', {}).get('enabled', True)

    # 微信配置

    @property
    def weixin_enabled(self) -> bool:
        """获取是否启用微信 Bot"""
        return self._config.get('weixin', {}).get('enabled', False)

    @property
    def weixin_accounts_file(self) -> str:
        """获取微信账号存储文件路径"""
        accounts_file = self._config.get('weixin', {}).get('accounts_file', './config/weixin_accounts.json')
        # 转换为绝对路径
        if not os.path.isabs(accounts_file):
            project_root = Path(__file__).parent.parent
            accounts_file = project_root / accounts_file
        return str(accounts_file)

    @property
    def weixin_context_tokens_file(self) -> str:
        """获取微信 context token 持久化文件路径"""
        tokens_file = self._config.get('weixin', {}).get('context_tokens_file', './shared/context_tokens.json')
        # 转换为绝对路径
        if not os.path.isabs(tokens_file):
            project_root = Path(__file__).parent.parent
            tokens_file = project_root / tokens_file
        return str(tokens_file)

    @property
    def weixin_message_splitting_enabled(self) -> bool:
        """获取微信是否启用消息按空行分割功能"""
        return self._config.get('weixin', {}).get('message_splitting', {}).get('enabled', False)

    @property
    def weixin_tool_use_notification_enabled(self) -> bool:
        """获取微信是否启用工具调用通知"""
        return self._config.get('weixin', {}).get('tool_use_notification', {}).get('enabled', False)

    @property
    def weixin_file_mapping_path(self) -> str:
        """获取微信文件映射表路径（独立于 Discord）"""
        mapping_file = self._config.get('weixin', {}).get('file_mapping_path', './shared/weixin_file_mapping.json')
        # 转换为绝对路径
        if not os.path.isabs(mapping_file):
            project_root = Path(__file__).parent.parent
            mapping_file = project_root / mapping_file
        return str(mapping_file)

    @property
    def file_mapping_path(self) -> str:
        """获取文件映射表路径"""
        mapping_file = self._config.get('file_mapping', {}).get('path', './file_mapping.json')
        # 转换为绝对路径
        if not os.path.isabs(mapping_file):
            project_root = Path(__file__).parent.parent
            mapping_file = project_root / mapping_file
        return str(mapping_file)

    # MCP 服务器配置

    @property
    def mcp_transport(self) -> str:
        """获取 MCP 服务器传输模式（stdio 或 http）"""
        return self._config.get('mcp_server', {}).get('transport', 'http')

    @property
    def mcp_host(self) -> str:
        """获取 MCP 服务器 HTTP 模式监听地址"""
        return self._config.get('mcp_server', {}).get('host', '0.0.0.0')

    @property
    def mcp_port(self) -> int:
        """获取 MCP 服务器 HTTP 模式监听端口"""
        return self._config.get('mcp_server', {}).get('port', 3334)

    @property
    def web_server_host(self) -> str:
        """获取 Web 服务器监听地址"""
        return self._config.get('web_server', {}).get('host', '0.0.0.0')

    @property
    def web_server_port(self) -> int:
        """获取 Web 服务器监听端口"""
        return self._config.get('web_server', {}).get('port', 8088)

    def update_discord_mention_required(self, value: bool):
        """更新 Discord mention_required 配置并持久化到文件"""
        # 更新内存中的配置
        if 'discord' not in self._config:
            self._config['discord'] = {}
        self._config['discord']['mention_required'] = value

        # 持久化到配置文件
        # 读取文件原始内容，找到 discord 段并更新 mention_required
        import re
        with open(self.config_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if re.search(r'mention_required:', content):
            # 已存在，更新值
            content = re.sub(
                r'mention_required:\s*\w+',
                f'mention_required: {str(value).lower()}',
                content
            )
        else:
            # 不存在，在 discord 段的 stickers_path 之后添加
            content = re.sub(
                r'(stickers_path:\s*[^\n]+)',
                f'\\1\n  # 是否需要 @机器人 才能触发对话\n  # true = 需要 @（默认），false = 不需要 @，任何消息都会触发\n  mention_required: {str(value).lower()}',
                content
            )

        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.write(content)
