"""
轮询微信 Bot 扫码登录状态并更新账号配置

使用方法：
    python poll_weixin_login.py <qrcode_id> [username]

参数：
    qrcode_id  - 二维码 ID（由 get_weixin_qrcode.py 获取）
    username   - 可选，要更新的用户名（如 "猪猪大王"）。如果不指定，仅打印登录结果不更新配置。

流程：
1. 轮询二维码扫描状态
2. 确认登录后打印 bot_id、bot_token、wxid
3. 如果指定了 username，自动更新 weixin_accounts.json 中的 bot_id 和 bot_token
"""
import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config


async def poll_status(qrcode: str):
    """轮询二维码状态，返回登录结果"""
    url = "https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status"
    params = {"qrcode": qrcode}

    import aiohttp
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(url, params=params) as resp:
                text = await resp.text()
                data = json.loads(text)

            status = data.get("status", "")
            ret = data.get("ret", -1)

            if ret != 0 and status != "wait":
                print(f"❌ 查询失败: {data}")
                return None

            if status == "confirmed":
                print("✅ 登录确认成功！")
                return data

            if status == "scaned":
                print("👀 已扫码，等待确认...")
            elif status == "expired":
                print("⚠️  二维码已过期")
                return None
            elif status == "wait":
                print("⏳ 等待扫码...")
            else:
                print(f"📊 状态: {status}")

            await asyncio.sleep(3)


def update_account(accounts_file: str, username: str, bot_id: str, bot_token: str):
    """更新指定用户的 bot_id 和 bot_token"""
    with open(accounts_file, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    updated = False
    for account in accounts:
        if account.get("username") == username:
            old_bot_id = account.get("bot_id")
            account["bot_id"] = bot_id
            account["bot_token"] = bot_token
            print(f"✅ 已更新 [{username}]: {old_bot_id} -> {bot_id}")
            updated = True
            break

    if not updated:
        print(f"❌ 未找到用户 [{username}]")
        return

    with open(accounts_file, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)

    print(f"✅ 配置已保存到 {accounts_file}")


async def main():
    if len(sys.argv) < 2:
        print("用法: python poll_weixin_login.py <qrcode_id> [username]")
        print("  qrcode_id  - 二维码 ID")
        print("  username   - 可选，要更新的用户名")
        sys.exit(1)

    qrcode = sys.argv[1]
    username = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"📡 轮询二维码状态: {qrcode}")

    result = await poll_status(qrcode)

    if not result:
        sys.exit(1)

    bot_id = result.get("ilink_bot_id", "")
    bot_token = result.get("bot_token", "")
    user_id = result.get("ilink_user_id", "")
    base_url = result.get("baseurl", "")

    print(f"\n📋 登录信息:")
    print(f"   Bot ID: {bot_id}")
    print(f"   Bot Token: {bot_token}")
    print(f"   User ID (wxid): {user_id}")
    print(f"   Base URL: {base_url}")

    if username:
        print(f"\n📝 更新用户 [{username}] 的配置...")
        config = Config()
        update_account(config.weixin_accounts_file, username, bot_id, bot_token)
    else:
        print("\n💡 未指定 username，跳过配置更新")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ 用户取消")
