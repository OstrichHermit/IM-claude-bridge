#!/usr/bin/env python3
"""
Discord Claude Bridge - 日志查看脚本
查看最近的错误和重要日志
"""
import re
from pathlib import Path
from datetime import datetime


def find_log_files():
    """查找日志文件"""
    project_root = Path(__file__).parent.parent.parent.parent
    project_dir = project_root / "discord-claude-bridge"

    # 查找可能的日志位置
    log_files = []

    # Python 日志文件
    log_files.extend(project_dir.glob("*.log"))
    log_files.extend(project_dir.glob("**/*.log"))

    # 控制台输出重定向文件
    log_files.extend(project_dir.glob("output*.txt"))

    return log_files


def parse_log_line(line):
    """解析日志行"""
    # 尝试匹配常见日志格式
    patterns = [
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',
        r'(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})',
        r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]',
    ]

    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)

    return None


def filter_errors(lines, level="ERROR"):
    """筛选错误日志"""
    error_keywords = {
        "ERROR": ["ERROR", "Error", "error", "Exception", "Traceback"],
        "WARN": ["WARNING", "WARN", "Warning", "warn"],
        "INFO": ["INFO", "info"],
    }

    keywords = error_keywords.get(level, error_keywords["ERROR"])

    filtered = []
    for line in lines:
        if any(keyword in line for keyword in keywords):
            filtered.append(line)

    return filtered


def show_recent_errors(log_file, lines=50):
    """显示最近的错误"""
    if not log_file.exists():
        print(f"❌ 日志文件不存在: {log_file}")
        return

    print(f"\n📄 读取日志: {log_file}")
    print("=" * 60)

    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()

    # 获取最后 N 行
    recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

    # 筛选错误
    errors = filter_errors(recent_lines, "ERROR")

    if not errors:
        print("✅ 未发现错误")
        return

    print(f"🔍 发现 {len(errors)} 条错误:\n")

    for error in errors:
        print(error.strip())


def show_all_logs(log_file, lines=50):
    """显示所有日志"""
    if not log_file.exists():
        print(f"❌ 日志文件不存在: {log_file}")
        return

    print(f"\n📄 读取日志: {log_file}")
    print("=" * 60)

    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()

    # 获取最后 N 行
    recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

    for line in recent_lines:
        print(line.strip())


def main():
    """主函数"""
    import sys

    log_files = find_log_files()

    if not log_files:
        print("❌ 未找到日志文件")
        print("\n💡 提示:")
        print("  - 将 stdout 重定向到文件: python bot.py > bot.log")
        print("  - 使用 Python logging 模块")
        return

    print(f"📂 找到 {len(log_files)} 个日志文件:")
    for i, log_file in enumerate(log_files, 1):
        print(f"  {i}. {log_file}")

    if len(log_files) == 0:
        return

    # 默认使用第一个日志文件
    log_file = log_files[0]

    command = sys.argv[1] if len(sys.argv) > 1 else "errors"

    if command == "errors":
        show_recent_errors(log_file)
    elif command == "all":
        show_all_logs(log_file)
    elif command == "file" and len(sys.argv) > 2:
        # 指定日志文件
        file_index = int(sys.argv[2]) - 1
        if 0 <= file_index < len(log_files):
            show_recent_errors(log_files[file_index])
        else:
            print(f"❌ 无效的文件索引: {file_index + 1}")
    else:
        print("用法:")
        print("  python view_logs.py errors              # 查看错误（默认）")
        print("  python view_logs.py all                 # 查看所有日志")
        print("  python view_logs.py errors [file_index] # 查看指定文件的错误")


if __name__ == "__main__":
    main()
