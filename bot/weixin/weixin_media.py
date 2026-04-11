"""
微信媒体文件下载和处理模块
支持从微信 CDN 下载并解密文件
"""
import os
import sys
import aiohttp
import hashlib
import base64
import json
import re
from typing import Optional, Dict, Any
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.logger import get_logger

log = get_logger("WeixinMedia", "weixin")


class MediaType:
    """消息类型常量"""
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class WeixinMediaDownloader:
    """微信媒体文件下载器"""

    DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

    def __init__(self, cdn_base_url: str = None):
        self.cdn_base_url = cdn_base_url or self.DEFAULT_CDN_BASE_URL

    async def download_and_decrypt(
        self,
        encrypt_query_param: str,
        aes_key: str,
        filekey: Optional[str] = None,
        timeout: int = 60
    ) -> bytes:
        """从 CDN 下载并解密文件

        Args:
            encrypt_query_param: 加密查询参数
            aes_key: AES 密钥（base64 或 hex 格式）
            filekey: 文件标识（可选）
            timeout: 超时时间（秒）

        Returns:
            解密后的文件内容
        """
        from urllib.parse import quote

        # 构建 CDN URL
        url = f"{self.cdn_base_url}/download?encrypted_query_param={quote(encrypt_query_param)}"
        if filekey:
            url += f"&filekey={quote(filekey)}"

        log.log(f"📥 正在下载: {url[:80]}...")

        # 下载加密数据
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"CDN 下载失败: HTTP {resp.status} - {error_text}")

                ciphertext = await resp.read()
                log.log(f"📥 下载了 {len(ciphertext)} 字节加密数据")

        # 解析 AES key
        aes_key_bytes = self._parse_aes_key(aes_key)

        # AES-128-ECB 解密
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)

        log.log(f"📥 解密后 {len(plaintext)} 字节")
        return plaintext

    def _parse_aes_key(self, aes_key: str) -> bytes:
        """解析 AES 密钥（支持 base64 和 hex 格式）"""
        # 尝试 base64
        try:
            return base64.b64decode(aes_key)
        except Exception:
            pass

        # 尝试 hex
        try:
            return bytes.fromhex(aes_key)
        except Exception:
            pass

        raise ValueError(f"无法解析 AES key: {aes_key[:20]}...")


class WeixinMediaHandler:
    """微信媒体文件处理器"""

    def __init__(self, save_dir: str, cdn_base_url: str = None):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.downloader = WeixinMediaDownloader(cdn_base_url)

    async def download_media_item(
        self,
        item: Dict[str, Any],
        label: str = "media"
    ) -> Optional[str]:
        """下载单个媒体项

        Args:
            item: 消息 item（包含 image_item/voice_item/file_item/video_item）
            label: 日志标签

        Returns:
            下载后的本地文件路径，失败返回 None
        """
        item_type = item.get("type")

        if item_type == MediaType.IMAGE:
            return await self._download_image(item, label)
        elif item_type == MediaType.FILE:
            return await self._download_file(item, label)
        elif item_type == MediaType.VOICE:
            return await self._download_voice(item, label)
        elif item_type == MediaType.VIDEO:
            return await self._download_video(item, label)

        return None

    async def _download_image(self, item: Dict[str, Any], label: str) -> Optional[str]:
        """下载图片"""
        image_item = item.get("image_item", {})
        media = image_item.get("media", {})

        encrypt_param = media.get("encrypt_query_param")
        if not encrypt_param:
            return None

        # 获取 AES key（优先使用 image_item.aeskey，其次使用 media.aes_key）
        aes_key = image_item.get("aeskey") or media.get("aes_key")
        if not aes_key:
            return None

        # image_item.aeskey 通常是 hex 格式，需要转换
        if image_item.get("aeskey"):
            # hex -> bytes -> base64
            try:
                key_bytes = bytes.fromhex(aes_key)
                aes_key = base64.b64encode(key_bytes).decode()
            except Exception:
                pass

        try:
            content = await self.downloader.download_and_decrypt(encrypt_param, aes_key)

            # 保存文件
            filename = f"weixin_image_{os.urandom(4).hex()}.png"
            filepath = self.save_dir / filename

            with open(filepath, "wb") as f:
                f.write(content)

            log.log(f"✅ [{label}] 图片已保存: {filepath}")
            return str(filepath)

        except Exception as e:
            log.log(f"❌ [{label}] 图片下载失败: {e}")
            return None

    async def _download_file(self, item: Dict[str, Any], label: str) -> Optional[str]:
        """下载文件"""
        file_item = item.get("file_item", {})
        media = file_item.get("media", {})

        encrypt_param = media.get("encrypt_query_param")
        aes_key = media.get("aes_key")
        filename = file_item.get("filename", "file.bin")

        if not encrypt_param or not aes_key:
            return None

        try:
            content = await self.downloader.download_and_decrypt(encrypt_param, aes_key)

            # 保存文件（使用安全的文件名）
            safe_filename = self._sanitize_filename(filename)
            filepath = self.save_dir / safe_filename

            # 如果文件名冲突，添加后缀
            counter = 1
            while filepath.exists():
                name, ext = os.path.splitext(safe_filename)
                filepath = self.save_dir / f"{name}_{counter}{ext}"
                counter += 1

            with open(filepath, "wb") as f:
                f.write(content)

            log.log(f"✅ [{label}] 文件已保存: {filepath}")
            return str(filepath)

        except Exception as e:
            log.log(f"❌ [{label}] 文件下载失败: {e}")
            return None

    async def _download_voice(self, item: Dict[str, Any], label: str) -> Optional[str]:
        """下载语音（SILK 格式）"""
        voice_item = item.get("voice_item", {})
        media = voice_item.get("media", {})

        encrypt_param = media.get("encrypt_query_param")
        aes_key = media.get("aes_key")

        if not encrypt_param or not aes_key:
            return None

        try:
            content = await self.downloader.download_and_decrypt(encrypt_param, aes_key)

            # 保存为 SILK 格式
            filename = f"weixin_voice_{os.urandom(4).hex()}.silk"
            filepath = self.save_dir / filename

            with open(filepath, "wb") as f:
                f.write(content)

            log.log(f"✅ [{label}] 语音已保存: {filepath}")
            return str(filepath)

        except Exception as e:
            log.log(f"❌ [{label}] 语音下载失败: {e}")
            return None

    async def _download_video(self, item: Dict[str, Any], label: str) -> Optional[str]:
        """下载视频"""
        video_item = item.get("video_item", {})
        media = video_item.get("media", {})

        encrypt_param = media.get("encrypt_query_param")
        aes_key = media.get("aes_key")

        if not encrypt_param or not aes_key:
            return None

        try:
            content = await self.downloader.download_and_decrypt(encrypt_param, aes_key)

            # 保存视频
            filename = f"weixin_video_{os.urandom(4).hex()}.mp4"
            filepath = self.save_dir / filename

            with open(filepath, "wb") as f:
                f.write(content)

            log.log(f"✅ [{label}] 视频已保存: {filepath}")
            return str(filepath)

        except Exception as e:
            log.log(f"❌ [{label}] 视频下载失败: {e}")
            return None

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """清理文件名，移除不安全字符"""
        # 保留扩展名
        name, ext = os.path.splitext(filename)

        # 只保留安全字符（字母、数字、下划线、连字符、中文字符）
        safe_name = re.sub(r'[^\w\u4e00-\u9fff\-_]', '_', name)

        # 限制长度
        if len(safe_name) > 100:
            safe_name = safe_name[:100]

        return f"{safe_name}{ext}"


class WeixinFileMapping:
    """微信文件映射表：file_size → filename"""

    def __init__(self, mapping_file: str):
        self.mapping_file = Path(mapping_file)
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        self.mapping: Dict[int, str] = {}  # file_size → filename

        self._load()

    def _load(self):
        """从文件加载映射表"""
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 迁移旧格式到新格式
                new_mapping = {}
                for key, value in data.items():
                    if isinstance(value, str):
                        # 可能是旧格式：message_id → filename，或者已经是 file_size → filename
                        try:
                            # 尝试将 key 解析为整数（file_size）
                            file_size = int(key)
                            new_mapping[file_size] = value
                        except ValueError:
                            # key 不是整数，跳过
                            pass
                    elif isinstance(value, dict):
                        # 中间格式：message_id → {filename, file_size, ...}
                        filename = value.get("filename")
                        file_size = value.get("file_size")
                        if filename and file_size:
                            new_mapping[file_size] = filename

                if new_mapping:
                    self.mapping = new_mapping
                    self._save()
                    log.log(f"📋 迁移了旧格式映射表到新格式")
                else:
                    self.mapping = {int(k): v for k, v in data.items() if isinstance(v, str)}

                log.log(f"📋 加载了 {len(self.mapping)} 条文件映射")
            except Exception as e:
                log.log(f"⚠️ 加载文件映射失败: {e}")
                self.mapping = {}

    def _save(self):
        """保存映射表到文件"""
        try:
            with open(self.mapping_file, 'w', encoding='utf-8') as f:
                json.dump(self.mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.log(f"⚠️ 保存文件映射失败: {e}")

    def add_file(self, filename: str, file_size: int) -> None:
        """添加文件映射

        Args:
            filename: 本地保存的文件名
            file_size: 文件大小（字节，作为查找键）
        """
        self.mapping[file_size] = filename
        self._save()
        log.log(f"📋 添加文件映射: {file_size} → {filename}")

    def get_filename_by_size(self, file_size: int) -> Optional[str]:
        """根据文件大小获取本地文件名

        Args:
            file_size: 文件大小（字节）

        Returns:
            本地文件名，不存在返回 None
        """
        return self.mapping.get(file_size)
