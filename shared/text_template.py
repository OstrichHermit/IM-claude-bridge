"""
文案模板占位符填充
用于 config.yaml 中可配置文案的 {placeholder} 占位符替换
"""


def fill_template(text: str, **context) -> str:
    """填充文案模板中的 {placeholder} 占位符

    支持占位符由调用方传入的 context 决定，常用：
    {username} {user_id} {channel_id} {channel_type} {channel_name}

    - 只替换 context 中提供的键，未提供的占位符保留原样（便于发现拼写错误）
    - 使用 str.replace 逐个替换，文案中的其他花括号内容不受影响
    """
    if not text:
        return text
    for key, value in context.items():
        if value is None:
            continue
        text = text.replace("{" + key + "}", str(value))
    return text
