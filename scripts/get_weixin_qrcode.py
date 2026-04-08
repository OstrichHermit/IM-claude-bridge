"""
快速获取微信 Bot 扫码登录二维码链接

使用方法：
    python get_weixin_qrcode.py

仅获取二维码链接并打印，不处理登录流程。
"""
import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    url = "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode"
    params = {"bot_type": "3"}

    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            text = await resp.text()
            data = json.loads(text)

            if data.get("ret") != 0:
                print(f"❌ 获取失败: {data}")
                return

            qrcode = data.get("qrcode", "")
            qrcode_img = data.get("qrcode_img_content", "")

            print(f"✅ 二维码 ID: {qrcode}")
            print(f"🔗 链接: {qrcode_img}")


if __name__ == "__main__":
    asyncio.run(main())
