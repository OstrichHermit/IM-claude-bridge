"""
统一文件发送 MCP 工具（轻量化版本）

通过 SessionWorker 走统一消息队列发送文件。
此工具只做参数验证，实际文件发送由 SessionWorker 和 Bot 处理。
"""
import os
import json
from typing import List


async def _send_files(file_paths: List[str]) -> str:
    """发送文件（通过 SessionWorker 统一消息队列）

    Args:
        file_paths: 文件路径列表（必需）

    Returns:
        JSON 格式的验证结果
    """
    if not file_paths:
        return json.dumps({
            "success": False,
            "message": "文件路径列表不能为空"
        }, ensure_ascii=False, indent=2)

    # 验证文件是否存在
    valid_files = []
    invalid_files = []

    for file_path in file_paths:
        if os.path.exists(file_path):
            valid_files.append(file_path)
        else:
            invalid_files.append(file_path)

    if not valid_files:
        return json.dumps({
            "success": False,
            "message": "所有文件都不存在",
            "invalid_files": invalid_files
        }, ensure_ascii=False, indent=2)

    result = {
        "success": True,
        "message": f"已验证 {len(valid_files)} 个文件，将通过消息队列发送",
        "valid_files": valid_files
    }

    if invalid_files:
        result["invalid_files"] = invalid_files
        result["message"] += f"，{len(invalid_files)} 个文件不存在已跳过"

    return json.dumps(result, ensure_ascii=False, indent=2)
