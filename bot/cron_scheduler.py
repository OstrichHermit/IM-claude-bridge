"""
Bot 定时任务调度器

在 Discord Bot 中运行的定时任务调度器，定期扫描任务文件并执行。
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor


class BotCronScheduler:
    """Bot 定时任务调度器"""

    def __init__(self, tasks_file: str):
        """
        初始化调度器

        Args:
            tasks_file: 任务文件路径（cron_jobs.json）
        """
        self.tasks_file = Path(tasks_file)
        self.scheduler = AsyncIOScheduler(executors={'default': AsyncIOExecutor()})
        self.tasks: Dict[str, dict] = {}
        self.running = False

    async def start(self):
        """启动调度器"""
        if self.running:
            return

        # 加载任务
        self._load_tasks()

        # 启用所有已启用的任务
        count = 0
        for job_id, job in self.tasks.items():
            if job.get('enabled', True):
                await self._schedule_job(job)
                count += 1

        self.scheduler.start()
        self.running = True
        print(f"⏰ 定时任务调度器已启动，已启用 {count}/{len(self.tasks)} 个任务")

    async def stop(self):
        """停止调度器"""
        if not self.running:
            return

        self.scheduler.shutdown(wait=False)
        self.running = False
        print("⏰ 定时任务调度器已停止")

    def _load_tasks(self):
        """从文件加载任务"""
        if not self.tasks_file.exists():
            self.tasks = {}
            return

        try:
            with open(self.tasks_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.tasks = {job['id']: job for job in data}
        except Exception as e:
            print(f"⚠️  加载定时任务失败: {e}")
            self.tasks = {}

    async def _schedule_job(self, job: dict):
        """调度单个任务"""
        try:
            job_id = job['id']
            cron_expr = job['cron_expr']

            # 移除旧的任务（如果存在）
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # 如果任务未启用，不调度
            if not job.get('enabled', True):
                return

            # 解析 cron 表达式并添加任务
            trigger = CronTrigger.from_crontab(cron_expr)
            self.scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=job_id,
                args=[job_id]
            )
            print(f"✓ 任务已调度: {job_id} ({cron_expr})")
        except Exception as e:
            print(f"⚠️  调度任务失败 {job.get('id')}: {e}")

    async def _execute_job(self, job_id: str):
        """执行任务（由调度器调用）"""
        job = self.tasks.get(job_id)
        if not job or not job.get('enabled', True):
            return

        is_one_time = not job.get('repeat', True)  # 是否为一次性任务
        task_type = "一次性任务" if is_one_time else "定时任务"
        print(f"⏰ 执行{task_type}: {job_id} - {job.get('description', '')}")

        try:
            # 调用 trigger_scheduled_task.py
            trigger_script = Path(__file__).parent.parent / "trigger_scheduled_task.py"

            # 准备配置内容
            config_content = f"""username={job['username']}
content<<<MARKER_START
{job['content']}
<<<MARKER_END
user_id={job.get('user_id', '')}
channel_id={job.get('channel_id', '')}
tag={job.get('tag', 'task')}
"""

            # 执行脚本
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(trigger_script),
                "--config-file", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path(__file__).parent.parent
            )

            stdout, stderr = await process.communicate(
                input=config_content.encode('utf-8')
            )

            if process.returncode != 0:
                raise Exception(f"脚本执行失败: {stderr.decode('utf-8')}")

            print(f"✅ {task_type}执行成功: {job_id}")

            # 更新执行记录
            job['last_run'] = datetime.now().isoformat()
            job['last_error'] = None

            # 如果是一次性任务，执行后自动禁用
            if is_one_time:
                job['enabled'] = False
                print(f"✓ 一次性任务已完成并禁用: {job_id}")

            self._save_tasks()

        except Exception as e:
            error_msg = str(e)
            print(f"❌ 任务执行失败 {job_id}: {error_msg}")

            # 更新错误记录
            job['last_run'] = datetime.now().isoformat()
            job['last_error'] = error_msg
            self._save_tasks()

    def _save_tasks(self):
        """保存任务到文件"""
        try:
            self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
            data = list(self.tasks.values())
            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存定时任务失败: {e}")

    async def reload_tasks(self):
        """重新加载任务文件（检测变化）"""
        old_task_ids = set(self.tasks.keys())
        self._load_tasks()
        new_task_ids = set(self.tasks.keys())

        # 检测新增任务
        added = new_task_ids - old_task_ids
        for job_id in added:
            job = self.tasks[job_id]
            if job.get('enabled', True):
                await self._schedule_job(job)
                print(f"✓ 检测到新任务: {job_id}")

        # 检测删除任务
        removed = old_task_ids - new_task_ids
        for job_id in removed:
            if job_id in self.scheduler:
                self.scheduler.remove_job(job_id)
                print(f"✓ 检测到任务删除: {job_id}")

        # 检测修改的任务（简单方式：重新调度所有任务）
        for job_id in self.tasks.keys():
            job = self.tasks[job_id]
            if job.get('enabled', True):
                await self._schedule_job(job)

        return len(added) + len(removed) > 0

    async def scan_loop(self):
        """定期扫描任务文件变化"""
        while self.running:
            try:
                # 每分钟检查一次
                await asyncio.sleep(60)

                # 重新加载任务
                await self.reload_tasks()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  扫描任务文件时出错: {e}")
                await asyncio.sleep(5)
