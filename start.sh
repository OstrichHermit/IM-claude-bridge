#!/bin/bash
# Discord Claude Bridge 启动脚本 (Linux/Mac)

echo "========================================"
echo "  Discord Claude Bridge 启动脚本"
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3，请先安装 Python 3.8+"
    exit 1
fi

# 检查配置文件
if [ ! -f "config/config.yaml" ]; then
    echo "[错误] 配置文件不存在！"
    echo "请先复制 config/config.example.yaml 为 config/config.yaml"
    echo "并填写您的 Discord Bot Token。"
    exit 1
fi

# 检查依赖
echo "[1/3] 检查 Python 依赖..."
if ! python3 -c "import discord, yaml" &> /dev/null; then
    echo "[提示] 正在安装依赖..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[错误] 依赖安装失败"
        exit 1
    fi
fi

# 创建 shared 目录（如果不存在）
mkdir -p shared

# 启动服务
echo "[2/3] 启动 Discord Bot..."
python3 bot/discord_bot.py &
BOT_PID=$!

sleep 2

echo "[3/3] 启动 Claude 桥接服务..."
python3 bridge/claude_bridge.py &
BRIDGE_PID=$!

echo ""
echo "========================================"
echo "  ✓ 所有服务已启动！"
echo "========================================"
echo ""
echo "Discord Bot PID: $BOT_PID"
echo "Claude Bridge PID: $BRIDGE_PID"
echo ""
echo "要停止服务，请运行: kill $BOT_PID $BRIDGE_PID"
echo "或按 Ctrl+C 停止脚本（会尝试关闭所有服务）"
echo ""

# 捕获退出信号
trap "echo ''; echo '正在停止服务...'; kill $BOT_PID $BRIDGE_PID 2>/dev/null; exit 0" INT TERM

# 等待进程
wait
