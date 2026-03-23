"""
微信协议客户端（独立实现，不依赖 OpenClaw）

直接调用微信 iLinkai API，实现扫码登录和消息收发。
"""
import aiohttp
import asyncio
import json
import base64
import struct
import os
import uuid
from typing import Optional, Dict, List, Any
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Message types
class MessageType:
    NONE = 0
    USER = 1
    BOT = 2


# Message states
class MessageState:
    NEW = 0
    GENERATING = 1
    FINISH = 2


@dataclass
class WeixinAccount:
    """微信账号信息"""
    bot_id: str
    bot_token: str
    base_url: str
    user_id: str
    # 新增：正向和反向映射字典
    user_mapping: Dict[str, str] = field(default_factory=dict)
    reverse_mapping: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """数据类初始化后自动调用，生成反向映射"""
        self.reverse_mapping = {v: k for k, v in self.user_mapping.items()}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于保存）"""
        return {
            "bot_id": self.bot_id,
            "bot_token": self.bot_token,
            "base_url": self.base_url,
            "user_id": self.user_id,
            "user_mapping": self.user_mapping  # 保存时带上映射
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WeixinAccount':
        """从字典创建"""
        return cls(
            bot_id=data["bot_id"],
            bot_token=data["bot_token"],
            base_url=data["base_url"],
            user_id=data["user_id"],
            user_mapping=data.get("user_mapping", {})  # 读取时提取映射
        )


class WeixinClient:
    """微信协议客户端"""

    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
    DEFAULT_BOT_TYPE = "3"

    def __init__(self, account: WeixinAccount):
        self.account = account
        self.session: Optional[aiohttp.ClientSession] = None
        self.get_updates_buf = ""

    @staticmethod
    def _generate_client_id() -> str:
        """生成 client_id"""
        return f"wx_{uuid.uuid4().hex[:16]}"

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    @staticmethod
    def _generate_wechat_uin() -> str:
        """生成 X-WECHAT-UIN header

        格式：随机 uint32 -> decimal string -> base64
        """
        uint32 = struct.unpack('>I', os.urandom(4))[0]
        return base64.b64encode(str(uint32).encode()).decode()

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.account.bot_token}",
            "X-WECHAT-UIN": self._generate_wechat_uin(),
        }

    async def get_updates(self, timeout_ms: int = 35000) -> Dict[str, Any]:
        """长轮询获取消息

        Args:
            timeout_ms: 超时时间（毫秒）

        Returns:
            API 响应数据
        """
        if not self.session:
            raise RuntimeError("Client session not initialized. Use async context manager.")

        url = f"{self.account.base_url}/ilink/bot/getupdates"
        payload = {
            "get_updates_buf": self.get_updates_buf,
            "base_info": {"channel_version": "1.0.0"}
        }

        headers = self._build_headers()

        try:
            async with self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000 + 5)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"getUpdates HTTP {resp.status}: {error_text}")
                    raise Exception(f"HTTP {resp.status}: {error_text}")

                # 微信 API 返回 octet-stream 但内容是 JSON
                raw_data = await resp.read()
                try:
                    data = json.loads(raw_data.decode('utf-8'))
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode getUpdates response: {raw_data[:200]}")
                    raise Exception(f"无法解析响应: Content-Type=application/octet-stream")

                # 详细日志：记录返回的数据
                logger.debug(f"getUpdates response: ret={data.get('ret')}, msgs count={len(data.get('msgs', []))}")

                ret = data.get("ret")
                if ret is not None and ret != 0:
                    errcode = data.get("errcode")
                    errmsg = data.get("errmsg", "未知错误")
                    logger.error(f"getUpdates error: {errcode} - {errmsg}")
                    raise Exception(f"API Error {errcode}: {errmsg}")

                # 更新游标
                self.get_updates_buf = data.get("get_updates_buf", "")
    
                # ===== 这里是你漏掉的拦截逻辑，应该放在这里 =====
                msgs = data.get("msgs", [])
                for msg in msgs:
                    raw_id = msg.get("from_user_id")
                    if raw_id:
                        # 查表，查不到就保持 raw_id
                        msg["from_user_id"] = self.account.user_mapping.get(raw_id, raw_id)
                # ==========================================

                return data

        except asyncio.TimeoutError:
            # 长轮询超时是正常的
            logger.debug("getUpdates timeout (normal)")
            raise
        except aiohttp.ClientError as e:
            logger.error(f"getUpdates network error: {e}")
            raise

    async def send_message(
        self,
        to_user_id: str,
        text: str,
        context_token: str = ""
    ) -> Dict[str, Any]:
        """发送文本消息

        Args:
            to_user_id: 接收者用户 ID
            text: 消息文本
            context_token: 上下文 token（用于回复消息）

        Returns:
            包含 message_id 的字典
        """
        if not self.session:
            raise RuntimeError("Client session not initialized. Use async context manager.")

        # 生成 client_id（用作返回的 message_id）
        client_id = self._generate_client_id()

        # --- 新增：发给微信前，将中文名还原回原始的 wxid ---
        real_user_id = self.account.reverse_mapping.get(to_user_id, to_user_id)
        
        url = f"{self.account.base_url}/ilink/bot/sendmessage"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": real_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT,  # 2 = BOT
                "message_state": MessageState.FINISH,  # 2 = FINISH
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {
                            "text": text
                        }
                    }
                ]
            },
            "base_info": {"channel_version": "1.0.0"}
        }

        headers = self._build_headers()

        try:
            async with self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"sendMessage HTTP {resp.status}: {error_text}")
                    raise Exception(f"HTTP {resp.status}: {error_text}")

                # 微信 API 返回 octet-stream 但内容是 JSON
                raw_data = await resp.read()
                try:
                    data = json.loads(raw_data.decode('utf-8'))
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode sendMessage response: {raw_data[:200]}")
                    raise Exception(f"无法解析响应: Content-Type=application/octet-stream")

                # 详细日志：记录返回的数据
                logger.debug(f"sendMessage response: ret={data.get('ret')}, errcode={data.get('errcode')}, errmsg={data.get('errmsg')}")
                logger.debug(f"Full API response: {data}")

                # 检查返回码
                ret = data.get("ret")
                if ret is not None and ret != 0:
                    errcode = data.get("errcode")
                    errmsg = data.get("errmsg") or data.get("msg") or "未知错误"
                    logger.error(f"sendMessage error: ret={ret}, errcode={errcode}, errmsg={errmsg}")
                    logger.error(f"Full API response: {data}")
                    raise Exception(f"API Error {errcode}: {errmsg}")
                
                # 微信 API 的响应是空的（{}），成功则返回生成的 client_id 作为 message_id
                return {"message_id": client_id}

        except aiohttp.ClientError as e:
            logger.error(f"sendMessage network error: {e}")
            raise

    async def test_connection(self) -> bool:
        """测试连接是否正常

        Returns:
            连接是否成功
        """
        try:
            # 尝试获取一次消息（应该会超时，但能验证 token 是否有效）
            await self.get_updates(timeout_ms=1000)
            return True
        except asyncio.TimeoutError:
            # 超时说明连接正常（只是没有消息）
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
