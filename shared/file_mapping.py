"""文件映射表管理模块

管理 Discord 附件 ID 和本地文件名的映射关系
"""
import json
from pathlib import Path
from typing import Optional
from threading import Lock


class FileMapping:
    """文件映射表管理器"""

    def __init__(self, mapping_file: str = None):
        """初始化文件映射表管理器

        Args:
            mapping_file: 映射表文件路径，默认在 discord-claude-bridge/file_mapping.json
        """
        if mapping_file is None:
            # 默认路径：discord-claude-bridge/file_mapping.json
            self.mapping_file = Path(__file__).parent.parent / "file_mapping.json"
        else:
            self.mapping_file = Path(mapping_file)

        self._mapping = {}
        self._lock = Lock()
        self._load()

    def _load(self):
        """从文件加载映射表"""
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file, 'r', encoding='utf-8') as f:
                    self._mapping = json.load(f)
                print(f"[文件映射] 已加载 {len(self._mapping)} 条映射记录")
            except Exception as e:
                print(f"[文件映射] 加载失败: {e}")
                self._mapping = {}
        else:
            self._mapping = {}
            print(f"[文件映射] 映射表文件不存在，创建新的映射表")

    def _save(self):
        """保存映射表到文件"""
        try:
            # 确保父目录存在
            self.mapping_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.mapping_file, 'w', encoding='utf-8') as f:
                json.dump(self._mapping, f, ensure_ascii=False, indent=2)
            print(f"[文件映射] 已保存 {len(self._mapping)} 条映射记录")
        except Exception as e:
            print(f"[文件映射] 保存失败: {e}")

    def get_local_filename(self, attachment_id: int) -> Optional[str]:
        """获取附件对应的本地文件名

        Args:
            attachment_id: Discord 附件 ID

        Returns:
            本地文件名，如果不存在则返回 None
        """
        with self._lock:
            return self._mapping.get(str(attachment_id))

    def set_local_filename(self, attachment_id: int, local_filename: str):
        """设置附件对应的本地文件名

        Args:
            attachment_id: Discord 附件 ID
            local_filename: 本地文件名
        """
        with self._lock:
            self._mapping[str(attachment_id)] = local_filename
            self._save()

    def remove(self, attachment_id: int):
        """移除附件映射

        Args:
            attachment_id: Discord 附件 ID
        """
        with self._lock:
            key = str(attachment_id)
            if key in self._mapping:
                del self._mapping[key]
                self._save()

    def clear(self):
        """清空所有映射"""
        with self._lock:
            self._mapping.clear()
            self._save()

    def get_all(self) -> dict:
        """获取所有映射

        Returns:
            映射字典 {attachment_id: local_filename}
        """
        with self._lock:
            return self._mapping.copy()
