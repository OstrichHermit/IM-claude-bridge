# Discord Bot 斜杠命令配置指南

## ⚠️ 重要：必须完成以下配置才能使用斜杠命令

### 步骤 1：Discord Developer Portal 配置

1. 访问 https://discord.com/developers/applications
2. 选择您的应用
3. 进入 **OAuth2** > **General**
4. 在 **Scopes** 部分，勾选：
   - ✅ **bot**
   - ✅ **applications.commands** ← 这个是必须的！
5. 复制生成的授权链接
6. 在浏览器中打开该链接，授权您的 Bot 加入服务器

### 步骤 2：Bot 权限配置

在 OAuth2 > URL Generator 中，确保勾选以下权限：
- ✅ Send Messages
- ✅ Embed Links
- ✅ Read Message History
- ✅ Add Reactions
- ✅ Use Slash Commands (applications.commands)

### 步骤 3：验证配置

运行测试脚本：

```bash
cd D:\AgentWorkspace\discord-claude-bridge
python test_slash_commands.py
```

如果成功，您会看到：
```
✅ 全局同步成功！已同步 1 个命令
⏱️  注意：全局命令可能需要 1-5 分钟才能生效
```

### 步骤 4：在 Discord 中测试

1. 等待 1-5 分钟（全局命令同步时间）
2. 在 Discord 中输入 `/`
3. 应该能看到 `/test` 命令出现

## 🚀 快速测试：服务器命令同步

如果不想等待全局命令生效，可以修改代码同步到特定服务器：

```python
# 在 bot/discord_bot.py 的 setup_hook 中
guild = discord.Object(id=你的服务器ID)  # 替换为你的服务器 ID
synced = await self.tree.sync(guild=guild)
```

服务器命令会**立即生效**，无需等待。

## 📋 如何获取服务器 ID

1. 在 Discord 中启用开发者模式
   - 设置 → 高级 → 开发者模式
2. 右键点击服务器名称
3. 选择"复制 ID"

## ❌ 常见问题

### 问题 1：命令不出现
- 确认已启用 `applications.commands` scope
- 等待 5 分钟让全局命令同步
- 尝试重新启动 Discord 客户端

### 问题 2：同步失败
- 检查 Bot Token 是否正确
- 确认网络连接正常
- 查看控制台错误信息

### 问题 3：命令出现但无响应
- 检查 Bot 是否在线
- 确认 Bot 有读取消息的权限
- 查看控制台日志

## 🎯 使用斜杠命令

配置完成后，在 Discord 中：
1. 输入 `/`
2. 选择命令：
   - `/new` - 新会话
   - `/status` - 查看状态
   - `/restart` - 重启服务
3. 按回车执行
