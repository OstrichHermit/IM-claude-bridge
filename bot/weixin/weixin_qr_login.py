"""
微信扫码登录模块（独立实现，不依赖 OpenClaw）

提供二维码获取、扫码等待、账号管理功能。
"""
import aiohttp
import asyncio
import json
import base64
import sys
from typing import Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.logger import get_logger

log = get_logger("WeixinQRLogin", "weixin")


@dataclass
class LoginResult:
    """登录结果"""
    success: bool
    bot_token: Optional[str] = None
    bot_id: Optional[str] = None
    base_url: Optional[str] = None
    user_id: Optional[str] = None
    error: Optional[str] = None

    def to_account(self, username: str = "") -> Optional['WeixinAccount']:
        """转换为 WeixinAccount

        Args:
            username: 用户名（如"用户名"）
        """
        if not self.success:
            return None
        from bot.weixin.weixin_client import WeixinAccount

        # 自动生成 user_id
        import zlib
        user_id = zlib.crc32(self.user_id.encode('utf-8')) % (10 ** 10)

        return WeixinAccount(
            bot_id=self.bot_id,
            bot_token=self.bot_token,
            base_url=self.base_url,
            wxid=self.user_id,
            username=username or "微信用户",
            user_id=user_id
        )


class WeixinQRLogin:
    """微信扫码登录"""

    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
    DEFAULT_BOT_TYPE = "3"
    QR_TIMEOUT_MS = 35000
    MAX_REFRESH = 3  # 最大二维码刷新次数

    @staticmethod
    async def get_qrcode(
        base_url: str = DEFAULT_BASE_URL,
        bot_type: str = DEFAULT_BOT_TYPE
    ) -> Tuple[str, str]:
        """
        获取二维码

        Args:
            base_url: API 基础地址
            bot_type: Bot 类型（默认 3）

        Returns:
            (qrcode_id, qrcode_image_url)

        Raises:
            Exception: 获取失败
        """
        url = f"{base_url}/ilink/bot/get_bot_qrcode"
        params = {"bot_type": bot_type}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log.log(f"getQRcode HTTP {resp.status}: {error_text}")
                    raise Exception(f"HTTP {resp.status}: {error_text}")

                # 微信 API 可能返回 JSON 或 octet-stream
                content_type = resp.headers.get('Content-Type', '')

                if 'application/json' in content_type:
                    # JSON 格式响应
                    data = await resp.json()
                    qrcode = data.get("qrcode")
                    qrcode_img = data.get("qrcode_img_content")

                    if not qrcode:
                        error = data.get("error", "未知错误")
                        log.log(f"getQRcode failed: {error}")
                        raise Exception(f"获取二维码失败: {error}")

                    log.log(f"QRcode obtained: {qrcode[:16]}...")
                    return qrcode, qrcode_img

                elif 'application/octet-stream' in content_type or 'image/png' in content_type:
                    # 可能是图片数据，也可能是 JSON（微信 API 的 Content-Type 不准确）
                    raw_data = await resp.read()

                    # 先尝试解析为 JSON
                    try:
                        data = json.loads(raw_data.decode('utf-8'))
                        qrcode = data.get("qrcode")
                        qrcode_img = data.get("qrcode_img_content")

                        if qrcode and qrcode_img:
                            log.log(f"QRcode obtained (JSON in octet-stream): {qrcode[:16]}...")
                            log.log(f"QRcode image URL: {qrcode_img[:100] if qrcode_img else 'N/A'}")
                            return qrcode, qrcode_img
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass

                    # 如果不是 JSON，当作图片数据处理
                    import uuid
                    qrcode = str(uuid.uuid4())

                    # 转换为 base64 data URL
                    import base64
                    qrcode_img = f"data:image/png;base64,{base64.b64encode(raw_data).decode()}"

                    log.log(f"QRcode obtained (image): {qrcode[:16]}...")
                    return qrcode, qrcode_img

                else:
                    # 未知格式，尝试作为文本解析（可能是 JSON 但 Content-Type 错误）
                    try:
                        text = await resp.text()
                        data = json.loads(text)

                        qrcode = data.get("qrcode")
                        qrcode_img = data.get("qrcode_img_content")

                        if not qrcode:
                            error = data.get("error", "未知错误")
                            log.log(f"getQRcode failed: {error}")
                            raise Exception(f"获取二维码失败: {error}")

                        log.log(f"QRcode obtained: {qrcode[:16]}...")
                        log.log(f"QRcode image URL: {qrcode_img[:100] if qrcode_img else 'N/A'}")
                        return qrcode, qrcode_img
                    except json.JSONDecodeError:
                        # 真的不是 JSON，返回原始数据
                        log.log(f"Unexpected response: {text[:200]}")
                        raise Exception(f"无法解析响应: Content-Type={content_type}")

    @staticmethod
    async def wait_for_scan(
        qrcode: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 300,
        on_status_change: Optional[callable] = None
    ) -> LoginResult:
        """
        等待扫码登录

        Args:
            qrcode: 二维码 ID
            base_url: API 基础地址
            timeout: 超时时间（秒）
            on_status_change: 状态变化回调函数 (status, data) -> None

        Returns:
            LoginResult
        """
        url = f"{base_url}/ilink/bot/get_qrcode_status"
        params = {"qrcode": qrcode}
        headers = {"iLink-App-ClientVersion": "1"}

        start_time = asyncio.get_event_loop().time()
        refresh_count = 0

        async with aiohttp.ClientSession() as session:
            while True:
                # 检查超时
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout:
                    log.log(f"QRcode login timeout after {timeout}s")
                    return LoginResult(
                        success=False,
                        error=f"扫码超时（{timeout}秒）"
                    )

                try:
                    async with session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=WeixinQRLogin.QR_TIMEOUT_MS / 1000 + 5)
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            log.log(f"getQRcodeStatus HTTP {resp.status}: {error_text}")
                            await asyncio.sleep(3)
                            continue

                        # 微信 API 返回 octet-stream 但内容是 JSON
                        raw_data = await resp.read()
                        try:
                            data = json.loads(raw_data.decode('utf-8'))
                        except json.JSONDecodeError:
                            log.log(f"Failed to decode response: {raw_data[:200]}")
                            await asyncio.sleep(3)
                            continue

                        status = data.get("status")
                        log.log(f"QRcode status: {status}")

                        # 回调状态变化
                        if on_status_change:
                            await on_status_change(status, data)

                        if status == "wait":
                            # 等待扫码
                            await asyncio.sleep(3)
                            continue

                        elif status == "scaned":
                            # 已扫码，等待确认
                            log.log("QRcode scanned, waiting for confirmation...")
                            await asyncio.sleep(2)
                            continue

                        elif status == "confirmed":
                            # 已确认，提取登录信息
                            bot_token = data.get("bot_token")
                            bot_id = data.get("ilink_bot_id")
                            base_url_resp = data.get("baseurl", base_url)
                            user_id = data.get("ilink_user_id")

                            if not bot_token or not bot_id:
                                log.log("Missing bot_token or bot_id in response")
                                return LoginResult(
                                    success=False,
                                    error="登录信息不完整"
                                )

                            log.log(f"Login success: bot_id={bot_id}, user_id={user_id}")
                            return LoginResult(
                                success=True,
                                bot_token=bot_token,
                                bot_id=bot_id,
                                base_url=base_url_resp,
                                user_id=user_id
                            )

                        elif status == "expired":
                            # 二维码过期
                            refresh_count += 1
                            if refresh_count > WeixinQRLogin.MAX_REFRESH:
                                log.log("QRcode expired too many times")
                                return LoginResult(
                                    success=False,
                                    error="二维码过期次数过多"
                                )

                            log.log(f"QRcode expired, refreshing ({refresh_count}/{WeixinQRLogin.MAX_REFRESH})...")

                            # 重新获取二维码
                            try:
                                new_qrcode, _ = await WeixinQRLogin.get_qrcode(base_url)
                                params["qrcode"] = new_qrcode
                                log.log(f"QRcode refreshed: {new_qrcode[:16]}...")
                                continue
                            except Exception as e:
                                log.log(f"Failed to refresh QRcode: {e}")
                                return LoginResult(
                                    success=False,
                                    error=f"刷新二维码失败: {e}"
                                )

                        else:
                            # 未知状态
                            log.log(f"Unknown status: {status}")
                            await asyncio.sleep(3)
                            continue

                except asyncio.TimeoutError:
                    # 长轮询超时，继续
                    log.log("QRcode polling timeout (normal)")
                    continue
                except aiohttp.ClientError as e:
                    log.log(f"QRcode polling error: {e}")
                    await asyncio.sleep(3)
                    continue
                except Exception as e:
                    log.log(f"QRcode polling unexpected error: {e}")
                    await asyncio.sleep(3)
                    continue


class WeixinAccountManager:
    """微信账号管理器"""

    def __init__(self, accounts_file: str):
        self.accounts_file = Path(accounts_file)
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)

    def load_accounts(self) -> list:
        """加载所有已保存的账号

        Returns:
            List[WeixinAccount]
        """
        from bot.weixin.weixin_client import WeixinAccount

        if not self.accounts_file.exists():
            return []

        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [WeixinAccount.from_dict(acc) for acc in data]
        except Exception as e:
            log.log(f"Failed to load accounts: {e}")
            return []

    def save_accounts(self, accounts: list):
        """保存账号列表

        Args:
            accounts: List[WeixinAccount]
        """
        try:
            data = [acc.to_dict() for acc in accounts]
            with open(self.accounts_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.log(f"Saved {len(accounts)} accounts to {self.accounts_file}")
        except Exception as e:
            log.log(f"Failed to save accounts: {e}")
            raise

    def add_account(self, account) -> bool:
        """添加新账号

        Args:
            account: WeixinAccount

        Returns:
            是否添加成功
        """
        accounts = self.load_accounts()

        # 检查是否已存在
        for acc in accounts:
            if acc.bot_id == account.bot_id:
                log.log(f"Account {account.bot_id} already exists")
                return False

        accounts.append(account)
        self.save_accounts(accounts)
        log.log(f"Added account: {account.bot_id}")
        return True

    def remove_account(self, bot_id: str) -> bool:
        """删除账号

        Args:
            bot_id: Bot ID

        Returns:
            是否删除成功
        """
        accounts = self.load_accounts()
        original_len = len(accounts)

        accounts = [acc for acc in accounts if acc.bot_id != bot_id]

        if len(accounts) == original_len:
            log.log(f"Account {bot_id} not found")
            return False

        self.save_accounts(accounts)
        log.log(f"Removed account: {bot_id}")
        return True
