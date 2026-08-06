"""
Discord AskUserQuestion View

负责渲染 AskUserQuestion 的按钮 + Modal 交互界面。
- 根据 questions 数组动态生成按钮（每问题一组选项 + 一个"自由输入"）
- 用户依次点击按钮回答（选项按钮直接选中；"自由输入"按钮弹 Modal）
- 用户可以随时改主意：再次点击其他按钮切换选中状态
- 用户点击"✅ 确认提交"显式提交，所有问题答完才允许提交
- 提交后把回答包装成新 PENDING message 入队，触发 SessionWorker 续接
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import discord

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.logger import get_logger
from shared.message_queue import (
    Message,
    MessageDirection,
    MessageStatus,
    MessageTag,
    ChannelType,
)

log = get_logger("DiscordBot", "discord")


# Discord API 限制
_BUTTON_LABEL_MAX = 80
_MODAL_INPUT_MAX = 1000
_EMBED_DESC_MAX = 4096
_EMBED_TITLE_MAX = 256


def _truncate(text: str, limit: int) -> str:
    """截断文本到指定长度（超出加省略号）"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def build_ask_embed(
    questions: list[dict], answers: Optional[dict[int, list[str]]] = None
) -> discord.Embed:
    """根据 questions 数组构造 AskUserQuestion Embed

    Args:
        questions: AskUserQuestion 工具的 input.questions 数组
        answers: 可选的当前回答 {q_idx: list[str]}，传入时会在 description 中
            标注每个选项是否被选中（✅）以及自由输入的回答内容
            - 单选问题：list 长度通常为 1（未答则为空 list 或不存在 key）
            - 多选问题：list 可变长度

    Returns:
        discord.Embed 实例
    """
    answers = answers or {}

    lines: list[str] = []
    for i, q in enumerate(questions):
        q_text = q.get("question", "（无问题）")
        lines.append(f"**问题 {i + 1}：** {q_text}")

        options = q.get("options", []) or []
        current_list = answers.get(i, [])
        option_labels = [opt.get("label", "") for opt in options]

        # 自由输入回答 = 不匹配任何选项 label 的回答（最多 1 条）
        free_answers = [a for a in current_list if a not in option_labels][:1]

        # 渲染选项
        for opt in options:
            label = opt.get("label", "无标签")
            desc = opt.get("description", "")
            prefix = "✅ " if label in current_list else "· "
            if desc:
                lines.append(f"  {prefix}{label} — {desc}")
            else:
                lines.append(f"  {prefix}{label}")

        # 渲染自由输入回答（多选时可能多条，每条单独一行）
        for fa in free_answers:
            truncated = _truncate(fa, 50)
            lines.append(f"  ✅ 自由输入: {truncated}")

        # 问题之间空一行
        if i < len(questions) - 1:
            lines.append("")

    description = "\n".join(lines)
    description = _truncate(description, _EMBED_DESC_MAX)

    embed = discord.Embed(
        title="❓ AskUserQuestion",
        description=description,
        color=discord.Color.gold(),
    )
    embed.set_footer(
        text="点击选项或「自由输入」回答 · 可随时改主意 · 答完点「✅ 确认提交」"
    )
    return embed


class FreeInputModal(discord.ui.Modal):
    """自由输入 Modal - 用户填入自定义文本作为回答

    自由输入只能保留 1 条（无论单选/多选）：
    - 单选：list = [自由输入回答]
    - 多选：list = [选项1, 选项2, ..., 自由输入回答]（提交新自由输入覆盖旧的）
    """

    def __init__(
        self,
        view: "AskQuestionView",
        q_idx: int,
        question_text: str,
        is_multi: bool = False,
    ):
        # 自由输入只能 1 条（无论单选/多选），统一 title
        title = f"自由输入 - 问题 {q_idx + 1}"
        title = _truncate(title, 45)  # Modal title 限 45 字符
        super().__init__(title=title)

        self._view = view
        self._q_idx = q_idx

        # 截断问题文本作为 label（label 限 45 字符）
        label = _truncate(question_text, 45)
        self.answer_input = discord.ui.TextInput(
            label=label or "你的回答",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=_MODAL_INPUT_MAX,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answer = self.answer_input.value or ""
        await self._view._select_free_input(interaction, self._q_idx, answer)


class AskQuestionView(discord.ui.View):
    """AskUserQuestion 的交互 View

    - 根据 questions 数组动态生成按钮（选项 + 自由输入 + 确认提交）
    - 用户可以随时改主意：点击新按钮即切换选中状态（同问题内互斥高亮）
    - 用户点击"✅ 确认提交"才提交，未答完会拒绝提交并提示
    """

    def __init__(
        self,
        session_key: str,
        questions: list[dict],
        message_queue,
        channel_id: int,
        channel_type: str,
        is_dm: bool,
        user_id: int,
        username: str,
        tool_use_id: str = "",
        asked_at: str = "",
    ):
        super().__init__(timeout=None)  # 永不超时（保留按钮可用）

        self._session_key = session_key
        self._questions = questions or []
        self._message_queue = message_queue
        self._channel_id = channel_id
        self._channel_type = channel_type
        self._is_dm = is_dm
        self._user_id = user_id
        self._username = username
        self._tool_use_id = tool_use_id
        self._asked_at = asked_at

        # key = q_idx (int), value = list of answers (str)
        # - 单选问题：list 长度始终为 1（未答则空 list 或不存在 key）
        # - 多选问题：list 可变长度（toggle 追加/移除）
        self._answers: dict[int, list[str]] = {}

        # 记录每个问题是否多选
        self._multi_select: dict[int, bool] = {}

        # 已提交标记（防止重复提交）
        self._submitted = False

        # 跟踪每个 q_idx 对应的按钮（便于切换样式），最后一项恒为自由输入按钮
        self._buttons_by_q: dict[int, list[discord.ui.Button]] = {}

        # 确认提交按钮（单独一行）
        self._confirm_button: Optional[discord.ui.Button] = None

        self._build_buttons()

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _build_buttons(self) -> None:
        """根据 questions 动态生成按钮

        discord.ui.View 限制：最多 25 个 Button，分 5 行（每行最多 5 个）。
        每个问题占独立的 row（≤4 问题 → row 0-3），每问题选项 ≤4 + 1 自由输入 = 5 按钮。
        确认提交按钮单独占 row = 问题数（最多 row 4）。
        """
        for q_idx, q in enumerate(self._questions):
            self._buttons_by_q[q_idx] = []
            self._multi_select[q_idx] = bool(q.get("multiSelect", False))

            options = q.get("options", []) or []

            for opt_idx, opt in enumerate(options):
                label = opt.get("label", f"选项{opt_idx + 1}")
                label = _truncate(label, _BUTTON_LABEL_MAX)

                btn = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    row=q_idx,  # 每个问题独占一行
                    custom_id=f"ask:{q_idx}:opt:{label}",
                )
                btn.callback = self._make_option_callback(q_idx, label)
                self.add_item(btn)
                self._buttons_by_q[q_idx].append(btn)

            # 自由输入按钮（灰色 secondary，不再用 primary）
            free_label = _truncate("自由输入", _BUTTON_LABEL_MAX)
            free_btn = discord.ui.Button(
                label=free_label,
                style=discord.ButtonStyle.secondary,
                row=q_idx,
                custom_id=f"ask:{q_idx}:free",
            )
            free_btn.callback = self._make_free_callback(q_idx)
            self.add_item(free_btn)
            self._buttons_by_q[q_idx].append(free_btn)

        # 确认提交按钮（绿色 success，单独一行）
        confirm_label = _truncate("✅ 确认提交", _BUTTON_LABEL_MAX)
        confirm_row = len(self._questions)  # 4 问题 → row 4
        confirm_btn = discord.ui.Button(
            label=confirm_label,
            style=discord.ButtonStyle.success,
            row=confirm_row,
            custom_id="ask:confirm",
        )
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)
        self._confirm_button = confirm_btn

    def _make_option_callback(self, q_idx: int, option_label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            await self._select_option(interaction, q_idx, option_label)

        return _callback

    def _make_free_callback(self, q_idx: int):
        async def _callback(interaction: discord.Interaction) -> None:
            q_text = self._questions[q_idx].get("question", f"问题 {q_idx + 1}")
            is_multi = self._multi_select.get(q_idx, False)
            modal = FreeInputModal(self, q_idx, q_text, is_multi=is_multi)
            await interaction.response.send_modal(modal)

        return _callback

    # ------------------------------------------------------------------
    # 回答处理（选中 + 视觉更新）
    # ------------------------------------------------------------------
    async def _select_option(
        self, interaction: discord.Interaction, q_idx: int, option_label: str
    ) -> None:
        """选中/取消选中某个问题的某个选项

        - 多选（multiSelect=true）：toggle 该选项（已存在则移除，否则追加）
        - 单选（multiSelect=false）：替换为该选项（同问题内其他按钮恢复 secondary）
        - 重写 Embed description 反映当前选中状态
        - edit_message 更新原消息
        """
        if self._submitted:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return

        current_list = self._answers.get(q_idx, [])
        is_multi = self._multi_select.get(q_idx, False)

        if is_multi:
            # 多选：toggle
            if option_label in current_list:
                current_list.remove(option_label)  # 取消选中
                log.log(
                    f"[AskUserQuestion] Q{q_idx + 1} 取消选项: {option_label[:50]}"
                )
            else:
                current_list.append(option_label)  # 选中
                log.log(
                    f"[AskUserQuestion] Q{q_idx + 1} 选中选项: {option_label[:50]}"
                )
            self._answers[q_idx] = current_list
        else:
            # 单选：替换
            self._answers[q_idx] = [option_label]
            log.log(
                f"[AskUserQuestion] Q{q_idx + 1} 选中选项: {option_label[:50]}"
            )
        await self._apply_visual_state(interaction)

    async def _select_free_input(
        self, interaction: discord.Interaction, q_idx: int, answer_text: str
    ) -> None:
        """追加/替换为某个问题的自由输入回答（自由输入只能 1 条）

        - 多选：保留选项回答 + 替换自由输入（list = [选项1, ..., 自由输入]）
        - 单选：替换为该文本（list = [自由输入回答]）
        """
        if self._submitted:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return

        is_multi = self._multi_select.get(q_idx, False)

        # 计算该问题的所有合法 option label
        options = self._questions[q_idx].get("options", []) or []
        option_labels = [opt.get("label", "") for opt in options]

        if is_multi:
            # 多选：保留选项回答 + 替换自由输入
            current_list = self._answers.get(q_idx, [])
            option_answers = [a for a in current_list if a in option_labels]
            self._answers[q_idx] = option_answers + [answer_text]
            log.log(
                f"[AskUserQuestion] Q{q_idx + 1} 自由输入（替换）: {answer_text[:50]}"
            )
        else:
            # 单选：替换为自由输入
            self._answers[q_idx] = [answer_text]
            log.log(
                f"[AskUserQuestion] Q{q_idx + 1} 自由输入: {answer_text[:50]}"
            )
        await self._apply_visual_state(interaction)

    async def _apply_visual_state(self, interaction: discord.Interaction) -> None:
        """根据 self._answers 同步按钮样式 + 重写 Embed，edit_message 更新原消息

        集中管理"选中高亮"逻辑：
        - 选项按钮：被选中的（label 在 current_list 里）变 primary
        - 自由输入按钮：有自由输入回答（list 里存在不匹配任何选项 label 的元素）→ primary
        - 其余按钮恢复 secondary
        - Embed description 中标注 ✅ 选中的选项，自由输入单独一行（最多 1 条）
        """
        # 1. 同步按钮样式
        for q_idx, btns in self._buttons_by_q.items():
            current_list = self._answers.get(q_idx, [])

            # _buttons_by_q 最后一项恒为自由输入按钮
            option_btns = btns[:-1]
            free_btn = btns[-1]

            # 选项按钮：label 在 current_list 里则 primary，否则 secondary
            for btn in option_btns:
                if btn.label in current_list:
                    btn.style = discord.ButtonStyle.primary
                else:
                    btn.style = discord.ButtonStyle.secondary

            # 自由输入按钮：检查 list 里是否有"不匹配任何选项 label"的元素
            # （单选和多选统一规则，有自由输入回答则蓝色 primary）
            option_btn_labels = [btn.label for btn in option_btns]
            free_answers = [a for a in current_list if a not in option_btn_labels]
            if free_answers:
                free_btn.style = discord.ButtonStyle.primary
            else:
                free_btn.style = discord.ButtonStyle.secondary

        # 2. 重写 Embed
        embed = build_ask_embed(self._questions, self._answers)

        # 3. edit_message
        try:
            if interaction.response.is_done():
                await interaction.followup.edit_message(
                    interaction.message.id, view=self, embed=embed
                )
            else:
                await interaction.response.edit_message(view=self, embed=embed)
        except Exception as e:
            log.log(f"⚠️ [AskUserQuestion] 更新消息失败: {e}")
            if not interaction.response.is_done():
                await interaction.response.defer()

    # ------------------------------------------------------------------
    # 提交确认
    # ------------------------------------------------------------------
    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        """确认提交按钮 callback

        - 未答完：ephemeral 提示还差几个问题
        - 答完：调用 _submit_answers
        """
        if self._submitted:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return

        if not self._all_answered():
            unanswered = sum(
                1
                for i in range(len(self._questions))
                if not self._answers.get(i)  # 空 list 或不存在
            )
            try:
                await interaction.response.send_message(
                    f"⚠️ 还有 {unanswered} 个问题未回答，请回答完再提交。",
                    ephemeral=True,
                )
            except Exception as e:
                log.log(f"⚠️ [AskUserQuestion] 提交校验提示失败: {e}")
                if not interaction.response.is_done():
                    await interaction.response.defer()
            return

        await self._submit_answers(interaction)

    def _all_answered(self) -> bool:
        """是否所有问题都已回答（每个问题的 list 非空）"""
        for i in range(len(self._questions)):
            if not self._answers.get(i):  # 空 list 或不存在
                return False
        return True

    async def _submit_answers(self, interaction: discord.Interaction) -> None:
        """所有问题答完，提交回答：clear pending_ask + 写新 PENDING message + 禁用所有按钮"""
        if self._submitted:
            return
        self._submitted = True

        # 1. 组装续接 prompt（list 语义：单选取 [0]，多选用逗号连接）
        prompt_lines = ["用户回答了之前的问题："]
        for i, q in enumerate(self._questions):
            q_text = q.get("question", f"问题 {i + 1}")
            ans_list = self._answers.get(i, [])
            if len(ans_list) == 1:
                ans_str = ans_list[0]
            elif len(ans_list) > 1:
                ans_str = " + ".join(ans_list)
            else:
                ans_str = "（未回答）"
            prompt_lines.append(f"{q_text} → {ans_str}")
        prompt = "\n".join(prompt_lines)

        # 2. clear pending_ask + ask_view_message_id（同时清两个字段；必须先 clear，
        # 否则 SessionWorker 不会消费续接消息；同时清掉 View ID 避免下次启动误恢复）
        try:
            self._message_queue.clear_ask_view(self._session_key)
        except Exception as e:
            log.log(f"❌ [AskUserQuestion] clear_ask_view 失败: {e}")

        # 3. 写新 PENDING message
        try:
            new_msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=prompt,
                status=MessageStatus.PENDING.value,
                discord_channel_id=self._channel_id,
                discord_message_id=0,  # 没有 Discord 消息 ID（来自按钮）
                discord_user_id=self._user_id,
                username=self._username,
                is_dm=self._is_dm,
                is_external=False,
                tag=MessageTag.DEFAULT.value,
                channel_type=self._channel_type,
            )
            new_id = self._message_queue.add_message(new_msg)
            log.log(
                f"✅ [AskUserQuestion] 续接消息已入队 #{new_id} (session={self._session_key})"
            )

            # 主动启动 typing indicator（因为 View callback 不走 on_message，不会自动触发）
            # 字段结构对齐 discord_sequence_sender.py 里 check_message_sequences 首次发现消息时的占位写法
            try:
                bot = interaction.client
                channel = interaction.channel
                if (
                    channel is not None
                    and hasattr(bot, "_maintain_typing_indicator")
                    and hasattr(bot, "pending_messages")
                ):
                    typing_task = asyncio.create_task(
                        bot._maintain_typing_indicator(channel)
                    )
                    bot.pending_messages[new_id] = {
                        "channel": channel,
                        "user_message": None,
                        "confirmation_msg": None,
                        "start_time": asyncio.get_event_loop().time(),
                        "content": prompt[:50],
                        "notified_processing": False,
                        "typing_task": typing_task,
                        "typing_active": True,
                    }
                    log.log(
                        f"✅ [AskUserQuestion] 已为 #{new_id} 启动 typing indicator"
                    )
            except Exception as e:
                log.log(
                    f"⚠️ [AskUserQuestion] 启动 typing 失败（不影响主流程）: {e}"
                )
        except Exception as e:
            log.log(f"❌ [AskUserQuestion] 入队续接消息失败: {e}")

        # 4. 禁用所有按钮（含确认按钮），更新原消息 Embed 染绿 + footer 加完成标记
        for btns in self._buttons_by_q.values():
            for btn in btns:
                btn.disabled = True
        if self._confirm_button is not None:
            self._confirm_button.disabled = True

        try:
            embed = (
                interaction.message.embeds[0]
                if interaction.message and interaction.message.embeds
                else build_ask_embed(self._questions, self._answers)
            )
            # 在 footer 追加已提交标记，染绿
            footer_text = (embed.footer.text or "") if embed.footer else ""
            embed.set_footer(text=f"{footer_text}\n✅ 已提交回答，等待 Claude 续接")
            embed.colour = discord.Color.green()
            if interaction.response.is_done():
                await interaction.followup.edit_message(
                    interaction.message.id, view=self, embed=embed
                )
            else:
                await interaction.response.edit_message(view=self, embed=embed)
        except Exception as e:
            log.log(f"⚠️ [AskUserQuestion] 提交后更新消息失败: {e}")
