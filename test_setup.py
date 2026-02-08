"""
配置测试脚本
验证 Discord Claude Bridge 的配置是否正确
"""
import sys
import asyncio
from pathlib import Path

# 添加 shared 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from shared.config import Config


def test_config():
    """测试配置文件"""
    print("=" * 50)
    print("  配置测试")
    print("=" * 50)
    print()

    try:
        config = Config()
        print("✅ 配置文件加载成功")
        print(f"   数据库路径: {config.database_path}")
        print(f"   轮询间隔: {config.poll_interval}ms")
        print()
        return True
    except FileNotFoundError as e:
        print(f"❌ 配置文件不存在")
        print(f"   请复制 config/config.example.yaml 为 config/config.yaml")
        print()
        return False
    except ValueError as e:
        print(f"❌ 配置错误: {e}")
        print()
        return False


def test_discord_token():
    """测试 Discord Token"""
    print("=" * 50)
    print("  Discord Token 测试")
    print("=" * 50)
    print()

    try:
        config = Config()
        token = config.discord_token
        if token and token != "YOUR_DISCORD_BOT_TOKEN_HERE":
            print(f"✅ Discord Token 已配置")
            print(f"   Token 长度: {len(token)} 字符")
            print()
            return True
        else:
            print(f"❌ Discord Token 未配置")
            print(f"   请在 config.yaml 中设置有效的 token")
            print()
            return False
    except Exception as e:
        print(f"❌ 错误: {e}")
        print()
        return False


def test_claude_cli():
    """测试 Claude Code CLI"""
    print("=" * 50)
    print("  Claude Code CLI 测试")
    print("=" * 50)
    print()

    import subprocess

    try:
        # 测试 claude 命令是否存在
        result = subprocess.run(
            ['claude', '--version'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            print("✅ Claude Code CLI 已安装")
            print(f"   版本信息: {result.stdout.strip()}")
            print()

            # 测试实际调用
            print("🧪 测试实际调用...")
            test_result = subprocess.run(
                ['claude', '-p', '请用两个字回复: 成功'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if test_result.returncode == 0:
                response = test_result.stdout.strip()
                if response:
                    print(f"✅ Claude CLI 调用成功")
                    print(f"   响应: {response[:100]}")
                    print()
                    return True
                else:
                    print(f"⚠️  Claude 返回空响应")
                    print(f"   可能需要先登录: claude setup-token")
                    print()
                    return False
            else:
                print(f"❌ Claude CLI 调用失败")
                print(f"   错误: {test_result.stderr}")
                print()
                return False
        else:
            print(f"❌ 找不到 claude 命令")
            print(f"   请安装 Claude Code: https://claude.ai/code")
            print()
            return False

    except FileNotFoundError:
        print(f"❌ 找不到 claude 命令")
        print(f"   请安装 Claude Code: https://claude.ai/code")
        print()
        return False
    except subprocess.TimeoutExpired:
        print(f"❌ Claude CLI 响应超时")
        print()
        return False
    except Exception as e:
        print(f"❌ 错误: {e}")
        print()
        return False


def test_database():
    """测试数据库"""
    print("=" * 50)
    print("  数据库测试")
    print("=" * 50)
    print()

    try:
        from shared.message_queue import MessageQueue

        config = Config()
        queue = MessageQueue(config.database_path)

        print("✅ 消息队列初始化成功")
        print(f"   数据库位置: {config.database_path}")
        print()
        return True

    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        print()
        return False


def main():
    """主测试函数"""
    print()
    print("🔍 Discord Claude Bridge 配置测试")
    print()

    results = {
        "配置文件": test_config(),
        "Discord Token": test_discord_token(),
        "Claude CLI": test_claude_cli(),
        "数据库": test_database(),
    }

    print("=" * 50)
    print("  测试结果汇总")
    print("=" * 50)
    print()

    all_passed = True
    for test_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("🎉 所有测试通过！可以启动服务了。")
        print("   运行: start.bat (Windows) 或 ./start.sh (Linux/Mac)")
    else:
        print("⚠️  部分测试失败，请根据提示修复问题。")

    print()


if __name__ == "__main__":
    main()
