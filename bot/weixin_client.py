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
    # CDN base URL（用于文件上传）
    cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c"
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
            "cdn_base_url": self.cdn_base_url,
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
            cdn_base_url=data.get("cdn_base_url", "https://novac2c.cdn.weixin.qq.com/c2c"),
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

    async def get_upload_url(
        self,
        filekey: str,
        media_type: int,
        to_user_id: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey: str,
        no_need_thumb: bool = True
    ) -> Dict[str, Any]:
        """获取上传 URL

        Args:
            filekey: 文件唯一标识
            media_type: 媒体类型 (1=图片, 2=视频, 3=文件, 4=语音)
            to_user_id: 接收者用户 ID
            rawsize: 原文件明文大小
            rawfilemd5: 原文件明文 MD5
            filesize: 加密后文件大小
            aeskey: AES 密钥（hex 格式）
            no_need_thumb: 是否不需要缩略图

        Returns:
            包含 upload_param 的响应
        """
        if not self.session:
            raise RuntimeError("Client session not initialized. Use async context manager.")

        url = f"{self.account.base_url}/ilink/bot/getuploadurl"
        payload = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": no_need_thumb,
            "aeskey": aeskey,
            "base_info": {"channel_version": "1.0.0"}
        }

        headers = self._build_headers()

        async with self.session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"getUploadUrl HTTP {resp.status}: {error_text}")
                raise Exception(f"HTTP {resp.status}: {error_text}")

            raw_data = await resp.read()
            data = json.loads(raw_data.decode('utf-8'))

            ret = data.get("ret")
            if ret is not None and ret != 0:
                errcode = data.get("errcode")
                errmsg = data.get("errmsg", "未知错误")
                logger.error(f"getUploadUrl error: {errcode} - {errmsg}")
                raise Exception(f"API Error {errcode}: {errmsg}")

            return data

    async def upload_to_cdn(
        self,
        file_path: str,
        upload_param: str,
        filekey: str,
        aeskey: bytes,
        filesize: int
    ) -> str:
        """上传文件到 CDN

        Args:
            file_path: 本地文件路径
            upload_param: 上传参数（从 getUploadUrl 获取）
            filekey: 文件唯一标识
            aeskey: AES 密钥
            filesize: 加密后文件大小

        Returns:
            下载加密参数（从响应头 x-encrypted-param 获取）
        """
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        from urllib.parse import quote

        if not self.session:
            raise RuntimeError("Client session not initialized. Use async context manager.")

        # 读取文件
        with open(file_path, 'rb') as f:
            plaintext = f.read()

        # AES-128-ECB 加密
        cipher = AES.new(aeskey, AES.MODE_ECB)
        ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))

        # 构建 CDN 上传 URL（参考 openclaw-weixin 的实现）
        # 使用账号配置中的 cdn_base_url
        cdn_url = f"{self.account.cdn_base_url}/upload?encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"

        logger.debug(f"CDN upload URL: {cdn_url}")
        logger.debug(f"Ciphertext size: {len(ciphertext)} bytes")

        # 构建请求头
        headers = {
            "Content-Type": "application/octet-stream",
        }

        # 上传到 CDN（直接发送二进制数据），添加重试机制
        max_retries = 3
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                async with self.session.post(
                    cdn_url,
                    data=ciphertext,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        error_msg = resp.headers.get("x-error-message", error_text)
                        logger.error(f"CDN upload HTTP {resp.status}: {error_msg}")

                        # 4xx 错误不重试
                        if 400 <= resp.status < 500:
                            raise Exception(f"CDN upload client error {resp.status}: {error_msg}")

                        # 5xx 错误重试
                        last_error = Exception(f"CDN upload server error: {error_msg}")
                        if attempt < max_retries:
                            logger.warning(f"CDN upload attempt {attempt} failed, retrying...")
                            await asyncio.sleep(2 ** attempt)  # 指数退避
                            continue
                        else:
                            raise last_error

                    # 从响应头获取下载参数
                    download_param = resp.headers.get("x-encrypted-param")
                    if not download_param:
                        logger.error("CDN response missing x-encrypted-param header")
                        if attempt < max_retries:
                            logger.warning(f"CDN upload attempt {attempt} missing param, retrying...")
                            await asyncio.sleep(1)
                            continue
                        else:
                            raise Exception("CDN upload response missing x-encrypted-param header")

                    logger.debug(f"CDN upload success on attempt {attempt}, got download_param: {download_param[:20]}...")
                    return download_param

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"CDN upload attempt {attempt} network error: {e}")
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                    continue
                else:
                    raise Exception(f"CDN upload failed after {max_retries} attempts: {e}")

        # 如果所有重试都失败了
        raise last_error if last_error else Exception("CDN upload failed")

    async def send_media_message(
        self,
        to_user_id: str,
        media_type: str,
        media_info: Dict[str, Any],
        context_token: str = "",
        **extra_fields
    ) -> Dict[str, Any]:
        """发送媒体消息（图片、视频、文件）

        Args:
            to_user_id: 接收者用户 ID
            media_type: 媒体类型 (image/video/file)
            media_info: 媒体信息，包含:
                - encrypt_query_param: CDN 下载参数
                - aes_key: AES 密钥（base64）
                - filesize_ciphertext: 加密后文件大小
            context_token: 上下文 token
            **extra_fields: 额外字段（如 file_name, video_size 等）

        Returns:
            包含 message_id 的字典
        """
        if not self.session:
            raise RuntimeError("Client session not initialized. Use async context manager.")

        # 生成 client_id
        client_id = self._generate_client_id()

        # 如果传入的是用户名，需要转换为原始 ID
        # 如果传入的已经是原始 ID（包含 @），直接使用
        if '@im.wechat' in to_user_id:
            # 已经是原始 ID，直接使用
            real_user_id = to_user_id
        else:
            # 是用户名，需要反向查找原始 ID
            real_user_id = self.account.reverse_mapping.get(to_user_id, to_user_id)

        # 构建消息项
        item_list = []

        if media_type == "image":
            item_list.append({
                "type": 2,  # IMAGE
                "image_item": {
                    "media": {
                        "encrypt_query_param": media_info["encrypt_query_param"],
                        "aes_key": media_info["aes_key"],
                        "encrypt_type": 1
                    },
                    "mid_size": media_info["filesize_ciphertext"]
                }
            })
        elif media_type == "video":
            item_list.append({
                "type": 5,  # VIDEO
                "video_item": {
                    "media": {
                        "encrypt_query_param": media_info["encrypt_query_param"],
                        "aes_key": media_info["aes_key"],
                        "encrypt_type": 1
                    },
                    "video_size": media_info["filesize_ciphertext"]
                }
            })
        elif media_type == "file":
            item_list.append({
                "type": 4,  # FILE
                "file_item": {
                    "media": {
                        "encrypt_query_param": media_info["encrypt_query_param"],
                        "aes_key": media_info["aes_key"],
                        "encrypt_type": 1
                    },
                    "file_name": extra_fields.get("file_name", "file"),
                    "len": str(extra_fields.get("filesize", 0))
                }
            })

        url = f"{self.account.base_url}/ilink/bot/sendmessage"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": real_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT,
                "message_state": MessageState.FINISH,
                "context_token": context_token,
                "item_list": item_list
            },
            "base_info": {"channel_version": "1.0.0"}
        }

        headers = self._build_headers()

        async with self.session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"sendMediaMessage HTTP {resp.status}: {error_text}")
                raise Exception(f"HTTP {resp.status}: {error_text}")

            raw_data = await resp.read()
            data = json.loads(raw_data.decode('utf-8'))

            ret = data.get("ret")
            if ret is not None and ret != 0:
                errcode = data.get("errcode")
                errmsg = data.get("errmsg") or data.get("msg") or "未知错误"
                logger.error(f"sendMediaMessage error: ret={ret}, errcode={errcode}, errmsg={errmsg}")
                raise Exception(f"API Error {errcode}: {errmsg}")

            return {"message_id": client_id}
