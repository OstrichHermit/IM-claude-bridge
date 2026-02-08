#!/usr/bin/env python3
"""
Discord Claude Bridge - 配置验证脚本
检查配置文件的正确性和完整性
"""
import sys
import yaml
from pathlib import Path


def get_config_path():
    """获取配置文件路径"""
    project_root = Path(__file__).parent.parent.parent.parent
    config_path = project_root / "discord-claude-bridge" / "config" / "config.yaml"
    return config_path


def load_config():
    """加载配置文件"""
    config_path = get_config_path()

    if not config_path.exists():
        return None, f"配置文件不存在: {config_path}"

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config, None
    except yaml.YAMLError as e:
        return None, f"YAML 格式错误: {e}"


def validate_discord_config(config):
    """验证 Discord 配置"""
    errors = []
    warnings = []

    discord = config.get('discord', {})

    # 检查 Token
    token = discord.get('token')
    if not token:
        errors.append("discord.token 未设置")
    elif token == "YOUR_DISCORD_BOT_TOKEN_HERE":
        errors.append("discord.token 仍是占位符，请设置真实的 Discord Bot Token")

    # 检查命令前缀
    prefix = discord.get('command_prefix')
    if not isinstance(prefix, str):
        errors.append("discord.command_prefix 必须是字符串")

    # 检查允许的频道
    channels = discord.get('allowed_channels')
    if not isinstance(channels, list):
        errors.append("discord.allowed_channels 必须是列表")
    elif channels and not all(isinstance(c, int) for c in channels):
        errors.append("discord.allowed_channels 必须是整数列表")

    # 检查允许的用户
    users = discord.get('allowed_users')
    if not isinstance(users, list):
        errors.append("discord.allowed_users 必须是列表")
    elif users and not all(isinstance(u, int) for u in users):
        errors.append("discord.allowed_users 必须是整数列表")

    return errors, warnings


def validate_claude_config(config):
    """验证 Claude 配置"""
    errors = []
    warnings = []

    claude = config.get('claude', {})

    # 检查可执行文件路径
    executable = claude.get('executable', 'claude')
    if not isinstance(executable, str):
        errors.append("claude.executable 必须是字符串")

    # 检查超时时间
    timeout = claude.get('timeout', 300)
    if not isinstance(timeout, int) or timeout <= 0:
        errors.append("claude.timeout 必须是正整数")

    # 检查重试次数
    max_retries = claude.get('max_retries', 3)
    if not isinstance(max_retries, int) or max_retries < 0:
        errors.append("claude.max_retries 必须是非负整数")

    # 检查会话模式
    session_mode = claude.get('session_mode', 'none')
    valid_modes = ['none', 'channel', 'user', 'global']
    if session_mode not in valid_modes:
        errors.append(f"claude.session_mode 必须是以下之一: {', '.join(valid_modes)}")

    # 检查工作目录
    working_dir = claude.get('working_directory', '')
    if working_dir and not isinstance(working_dir, str):
        errors.append("claude.working_directory 必须是字符串")

    return errors, warnings


def validate_queue_config(config):
    """验证队列配置"""
    errors = []
    warnings = []

    queue = config.get('queue', {})

    # 检查数据库路径
    db_path = queue.get('database_path', './shared/messages.db')
    if not isinstance(db_path, str):
        errors.append("queue.database_path 必须是字符串")

    # 检查轮询间隔
    poll_interval = queue.get('poll_interval', 500)
    if not isinstance(poll_interval, int) or poll_interval <= 0:
        errors.append("queue.poll_interval 必须是正整数")
    elif poll_interval < 100:
        warnings.append("queue.poll_interval 小于 100ms 可能导致高 CPU 占用")

    # 检查消息保留时间
    retention = queue.get('message_retention_hours', 24)
    if not isinstance(retention, int) or retention < 0:
        errors.append("queue.message_retention_hours 必须是非负整数")

    return errors, warnings


def test_database_connection(config):
    """测试数据库连接"""
    import sqlite3

    db_path = config.get('queue', {}).get('database_path', './shared/messages.db')

    # 转换为绝对路径
    project_root = Path(__file__).parent.parent.parent.parent / "discord-claude-bridge"
    db_path = project_root / db_path

    if not db_path.exists():
        # 如果数据库不存在，尝试创建
        parent_dir = db_path.parent
        if not parent_dir.exists():
            try:
                parent_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return False, f"无法创建数据库目录: {e}"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 测试查询
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()

        conn.close()
        return True, f"数据库连接成功，包含 {len(tables)} 个表"
    except Exception as e:
        return False, f"数据库连接失败: {e}"


def test_claude_cli(config):
    """测试 Claude CLI 是否可用"""
    import subprocess

    executable = config.get('claude', {}).get('executable', 'claude')

    try:
        result = subprocess.run(
            [executable, '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            return True, f"Claude CLI 可用: {result.stdout.strip()}"
        else:
            return False, f"Claude CLI 返回错误: {result.stderr}"
    except FileNotFoundError:
        return False, f"Claude CLI 未找到: {executable}"
    except Exception as e:
        return False, f"Claude CLI 测试失败: {e}"


def main():
    """主函数"""
    print("=" * 60)
    print("Discord Claude Bridge - 配置验证")
    print("=" * 60)

    # 加载配置
    config, error = load_config()
    if error:
        print(f"❌ {error}")
        return 1

    print("✅ 配置文件加载成功")

    # 验证配置
    all_errors = []
    all_warnings = []

    discord_errors, discord_warnings = validate_discord_config(config)
    all_errors.extend(discord_errors)
    all_warnings.extend(discord_warnings)

    claude_errors, claude_warnings = validate_claude_config(config)
    all_errors.extend(claude_errors)
    all_warnings.extend(claude_warnings)

    queue_errors, queue_warnings = validate_queue_config(config)
    all_errors.extend(queue_errors)
    all_warnings.extend(queue_warnings)

    # 显示结果
    if all_errors:
        print("\n❌ 发现配置错误:")
        for error in all_errors:
            print(f"  - {error}")
    else:
        print("\n✅ 配置格式正确")

    if all_warnings:
        print("\n⚠️  配置警告:")
        for warning in all_warnings:
            print(f"  - {warning}")

    # 测试数据库连接
    print("\n📊 测试数据库连接...")
    db_success, db_message = test_database_connection(config)
    if db_success:
        print(f"✅ {db_message}")
    else:
        print(f"❌ {db_message}")
        all_errors.append(db_message)

    # 测试 Claude CLI
    print("\n🤖 测试 Claude CLI...")
    claude_success, claude_message = test_claude_cli(config)
    if claude_success:
        print(f"✅ {claude_message}")
    else:
        print(f"❌ {claude_message}")
        all_errors.append(claude_message)

    # 总结
    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ 验证失败: 发现 {len(all_errors)} 个错误")
        return 1
    else:
        print("✅ 验证通过！配置文件正确")
        return 0


if __name__ == "__main__":
    sys.exit(main())
