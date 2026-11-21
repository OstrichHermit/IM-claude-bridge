"""
上下文用量监控模块（纯函数）

bridge 以 `claude -p --verbose --output-format stream-json` 方式调用 Claude Code，
输出流里每条 assistant 事件都自带 message.usage。本模块负责：
- 把单条 usage 换算成"当前上下文已用 tokens"（算法与 Claude Code 内部一致）
- 格式化百分比 / 进度条 / 展示文本

进度条算法与智谱额度条目（quota_monitor.fetch_quota_summary）完全一致，共 10 格。
"""

DEFAULT_CONTEXT_WINDOW = 1_000_000  # 模型名带 [1m] 时 Claude Code 按 100 万窗口算


def usage_to_context_tokens(usage) -> int:
    """单条 assistant 消息的 usage → 当前上下文 tokens

    算法：input_tokens + cache_creation_input_tokens + cache_read_input_tokens（output_tokens 不计入）。
    非 dict、字段缺失、字段为 None 或非数字，都按 0 算。

    注意：同一次 API 响应含多个 content block 时，流里会出现多条 assistant 事件且 usage 完全相同，
    调用方只能取"最后一条 assistant 事件的 usage"再交给本函数，绝不能把多条累加。
    """
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        # bool 是 int 的子类，但 True/False 不是合法的 token 数，一并排除
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += int(value)
    return total


def format_window_tokens(window_tokens: int) -> str:
    """把窗口大小格式化成简短写法：1000000 → "1M"，1500000 → "1.5M"，200000 → "200K"，不足 1000 原样显示"""
    if window_tokens >= 1_000_000:
        return f"{window_tokens / 1_000_000:g}M"
    if window_tokens >= 1_000:
        return f"{window_tokens / 1_000:g}K"
    return str(window_tokens)


def _resolve_window(window_tokens) -> int:
    """窗口值非法（None / 非数字 / 非正数）时回退默认窗口，不抛异常"""
    try:
        value = int(window_tokens)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_WINDOW
    return value if value > 0 else DEFAULT_CONTEXT_WINDOW


def context_percentage(used_tokens: int, window_tokens: int) -> int:
    """上下文已用百分比：round(used / window * 100)，限制在 0~100；window <= 0 或非法时按默认窗口算"""
    window = _resolve_window(window_tokens)
    percentage = round(used_tokens / window * 100)
    return max(0, min(100, percentage))


def format_usage_text(used_tokens: int, window_tokens: int) -> str:
    """用量文字："已用 12% / 1M Tokens"（提示词注入行和展示文本共用同一段写法）"""
    window = _resolve_window(window_tokens)
    return f"已用 {context_percentage(used_tokens, window)}% / {format_window_tokens(window)} Tokens"


def build_context_bar(used_tokens: int, window_tokens: int) -> tuple[str, str]:
    """返回（进度条, 文字）

    进度条算法与智谱额度条目完全一致（10 格，四舍五入到整格）。
    文字格式：上下文 已用 12% / 1M Tokens
    """
    window = _resolve_window(window_tokens)
    percentage = context_percentage(used_tokens, window)
    bar = "▓" * int(percentage / 10 + 0.5)
    bar = bar + "░" * max(0, 10 - len(bar))
    return bar, f"上下文 {format_usage_text(used_tokens, window)}"


def format_context_usage(used_tokens: int, window_tokens: int) -> str:
    """返回 "{进度条} {文字}"

    例：▓░░░░░░░░░ 上下文 已用 12% / 1M Tokens
    """
    bar, text = build_context_bar(used_tokens, window_tokens)
    return f"{bar} {text}"
