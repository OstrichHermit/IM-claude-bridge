"""
智谱 GLM Coding Plan 剩余额度查询模块
"""
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared.logger import get_logger

log = get_logger("QuotaMonitor", "bridge")

QUOTA_API_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def fetch_quota_summary() -> Optional[str]:
    """查询智谱 GLM Coding Plan 剩余额度，返回格式化摘要（任何失败返回 None，绝不抛异常）"""
    try:
        # 读取 API Key：优先环境变量，其次 Claude Code 配置
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if not api_key:
            with open(CLAUDE_SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
            api_key = (settings.get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if not api_key:
            log.log("⚠️ 未找到智谱 API Key（ANTHROPIC_AUTH_TOKEN），跳过额度查询")
            return None

        req = urllib.request.Request(
            QUOTA_API_URL,
            headers={
                "Authorization": api_key,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        limits = (data.get("data") or {}).get("limits") or []
        for item in limits:
            if item.get("type") == "TOKENS_LIMIT":
                percentage = item.get("percentage")
                next_reset = item.get("nextResetTime")
                if percentage is None or next_reset is None:
                    break
                bar = "￭" * round(percentage / 10)
                bar = bar + "･" * max(0, 10 - len(bar))
                reset_str = datetime.fromtimestamp(next_reset / 1000).strftime("%H:%M")
                return f"{bar} 已用 {percentage}% / {reset_str} 重置"

        log.log("⚠️ 额度接口响应中没有 TOKENS_LIMIT 条目")
        return None
    except Exception as e:
        log.log(f"⚠️ 查询智谱额度失败: {e}")
        return None
