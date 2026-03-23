"""
微信扫码登录工具

使用方法：
    python login_weixin.py

流程：
1. 获取二维码
2. 显示二维码（终端或保存为图片）
3. 等待扫码登录
4. 保存账号信息到配置文件
"""
import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.weixin_qr_login import WeixinQRLogin, WeixinAccountManager
from shared.config import Config


def display_qrcode_in_terminal(qrcode_image_url: str):
    """在终端显示二维码并打开浏览器"""
    print("\n" + "=" * 50)
    print("📱 请使用微信扫描二维码登录：")
    print("=" * 50)

    # 检查是否是 URL 格式
    if qrcode_image_url.startswith("http://") or qrcode_image_url.startswith("https://"):
        # URL 格式，直接打印并在浏览器中打开
        print(f"\n🔗 二维码链接: {qrcode_image_url}")
        print(f"💡 正在尝试在浏览器中打开...\n")

        try:
            import webbrowser
            webbrowser.open(qrcode_image_url)
            print("✅ 已在浏览器中打开二维码页面")
        except:
            print("⚠️  无法自动打开浏览器，请手动复制链接到浏览器")

        print("\n⏳ 等待扫码中...\n")
        return

    # Base64 图片格式（不再保存，直接提示）
    # qrcode_image_url 格式：data:image/png;base64,xxx
    if "," in qrcode_image_url:
        print("\n⚠️  检测到 Base64 图片格式，但不再保存为文件")
        print("💡 请使用微信扫描浏览器中的二维码")

    print("\n⏳ 等待扫码中...\n")


async def status_callback(status: str, data: dict):
    """状态变化回调"""
    status_messages = {
        "wait": "⏳ 等待扫码...",
        "scaned": "👀 已扫码，等待确认...",
        "confirmed": "✅ 已确认，登录成功！",
        "expired": "⚠️  二维码已过期，正在刷新..."
    }

    msg = status_messages.get(status, f"❓ 未知状态: {status}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


async def main():
    """主函数"""
    print("\n" + "=" * 50)
    print("🚀 微信扫码登录工具")
    print("=" * 50 + "\n")

    # 加载配置
    config = Config()
    accounts_file = config.weixin_accounts_file

    print(f"📋 账号文件: {accounts_file}")

    # 创建账号管理器
    manager = WeixinAccountManager(accounts_file)

    # 检查已有账号
    existing_accounts = manager.load_accounts()
    if existing_accounts:
        print(f"\n📱 已有 {len(existing_accounts)} 个账号：")
        for acc in existing_accounts:
            print(f"   - Bot ID: {acc.bot_id}")
            print(f"     User ID: {acc.user_id}")

        choice = input("\n是否继续添加新账号？(y/N): ").strip().lower()
        if choice != 'y':
            print("❌ 取消登录")
            return

    # 获取二维码
    print("\n📡 正在获取二维码...")
    try:
        qrcode, qrcode_img = await WeixinQRLogin.get_qrcode()
        print(f"✅ 二维码获取成功: {qrcode[:16]}...")
    except Exception as e:
        print(f"❌ 获取二维码失败: {e}")
        return

    # 显示二维码
    display_qrcode_in_terminal(qrcode_img)

    # 等待扫码
    print("💡 提示：扫码后请在手机上确认登录\n")

    try:
        result = await WeixinQRLogin.wait_for_scan(
            qrcode,
            timeout=300,  # 5分钟超时
            on_status_change=status_callback
        )

        if result.success:
            print("\n" + "=" * 50)
            print("✅ 登录成功！")
            print("=" * 50)
            print(f"Bot ID: {result.bot_id}")
            print(f"wxid: {result.user_id}")
            print(f"Base URL: {result.base_url}")

            # 获取用户配置
            print("\n" + "=" * 50)
            print("📝 请配置用户信息")
            print("=" * 50)

            username = input("\n请输入用户名（如：鸵鸟居士）: ").strip()
            if not username:
                print("⚠️  用户名不能为空，使用默认值：微信用户")
                username = "微信用户"

            # 自动计算 user_id
            import zlib
            user_id = zlib.crc32(result.user_id.encode('utf-8')) % (10 ** 10)
            print(f"✅ 自动生成用户ID: {user_id}")

            print(f"\n✅ 用户配置完成：")
            print(f"   用户名: {username}")
            print(f"   用户ID: {user_id}")

            # 保存账号（to_account 内部会自动计算 user_id）
            account = result.to_account(username=username)

            if manager.add_account(account):
                print(f"\n✅ 账号已保存到: {accounts_file}")
                print("\n💡 现在可以启动微信 Bot 了！")
            else:
                print("\n⚠️  账号已存在，未重复保存")

        else:
            print("\n" + "=" * 50)
            print("❌ 登录失败")
            print("=" * 50)
            print(f"错误: {result.error}")

    except Exception as e:
        print(f"\n❌ 登录过程出错: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ 用户取消登录")
