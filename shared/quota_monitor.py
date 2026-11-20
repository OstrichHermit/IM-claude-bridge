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
# CREDIT_LIMIT 的 unit 时间单位（按接口返回的 nextResetTime 反推）
CREDIT_WINDOW_UNITS = {3: "小时", 6: "个月"}


def fetch_quota_summary() -> Optional[str]:
    """查询智谱 GLM Coding Plan 剩余额度，返回格式化摘要（任何失败返回 None，绝不抛异常）；TOKENS_LIMIT 一条、CREDIT_LIMIT 两条，每条一行"""
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
        lines = []
        for item in limits:
            item_type = item.get("type")
            percentage = item.get("percentage")
            next_reset = item.get("nextResetTime")
            if percentage is None or next_reset is None:
                continue
            if item_type == "TOKENS_LIMIT":
                window = ""
            elif item_type == "CREDIT_LIMIT":
                unit = CREDIT_WINDOW_UNITS.get(item.get("unit"), f"单位{item.get('unit')}")
                window = f"{item.get('number')}{unit} "
            else:
                continue
            bar = "▓" * int(percentage / 10 + 0.5)
            bar = bar + "░" * max(0, 10 - len(bar))
            reset_dt = datetime.fromtimestamp(next_reset / 1000)
            reset_str = reset_dt.strftime("%H:%M") if reset_dt.date() == datetime.now().date() else reset_dt.strftime("%m-%d %H:%M")
            lines.append(f"{bar} {window}已用 {percentage}% / {reset_str} 重置")

        if not lines:
            log.log("⚠️ 额度接口响应中没有 TOKENS_LIMIT / CREDIT_LIMIT 条目")
            return None
        return "\n".join(lines)
    except Exception as e:
        log.log(f"⚠️ 查询智谱额度失败: {e}")
        return None
