"""
定时任务调度器
集成到 discord-claude-bridge 的轻量级 cron 调度系统
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor


@dataclass
class CronJob:
    """定时任务数据结构"""
    id: str
    cron_expr: str          # cron 表达式，如 "0 9 * * *"
    content: str            # 提示词内容
    username: str           # 用户名
    user_id: Optional[str]  # Discord 用户 ID
    channel_id: Optional[str] # Discord 频道 ID
    tag: str                # 标签（task/reminder）
    description: str        # 任务描述
    enabled: bool = True    # 是否启用
    created_at: str = None  # 创建时间
    last_run: str = None    # 最后运行时间
    last_error: str = None  # 最后错误信息

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)


class CronStore:
    """任务持久化存储（JSON 文件）"""

    def __init__(self, storage_path: str):
        self.storage_file = Path(storage_path) / "cron_jobs.json"
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, CronJob] = {}
        self.load()

    def load(self):
        """从文件加载任务"""
        if not self.storage_file.exists():
            return

        try:
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for job_data in data:
                    job = CronJob.from_dict(job_data)
                    self.jobs[job.id] = job
        except Exception as e:
            print(f"⚠️  加载定时任务失败: {e}")

    def save(self):
        """保存任务到文件"""
        try:
            data = [job.to_dict() for job in self.jobs.values()]
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存定时任务失败: {e}")

    def add(self, job: CronJob):
        """添加任务"""
        self.jobs[job.id] = job
        self.save()

    def remove(self, job_id: str) -> bool:
        """删除任务"""
        if job_id in self.jobs:
            del self.jobs[job_id]
            self.save()
            return True
        return False

    def get(self, job_id: str) -> Optional[CronJob]:
        """获取任务"""
        return self.jobs.get(job_id)

    def list_all(self) -> List[CronJob]:
        """列出所有任务"""
        return list(self.jobs.values())

    def update(self, job: CronJob):
        """更新任务"""
        if job.id in self.jobs:
            self.jobs[job.id] = job
            self.save()

    def mark_run(self, job_id: str, error: Optional[str] = None):
        """标记任务执行"""
        job = self.jobs.get(job_id)
        if job:
            job.last_run = datetime.now().isoformat()
            job.last_error = error
            self.save()


class MCPScheduler:
    """MCP 定时任务调度器"""

    def __init__(self, storage_path: str, executor: Callable):
        """
        初始化调度器

        Args:
            storage_path: 存储路径
            executor: 任务执行器（接收 CronJob 对象）
        """
        self.store = CronStore(storage_path)
        self.executor = executor
        self.scheduler = AsyncIOScheduler(executors={'default': AsyncIOExecutor()})
        self.running = False

    async def start(self):
        """启动调度器"""
        if self.running:
            return

        # 加载并启用所有已启用的任务
        jobs = self.store.list_all()
        enabled_count = 0
        for job in jobs:
            if job.enabled:
                await self._schedule_job(job)
                enabled_count += 1

        self.scheduler.start()
        self.running = True
        print(f"⏰ 定时任务调度器已启动，已启用 {enabled_count}/{len(jobs)} 个任务")

    async def stop(self):
        """停止调度器"""
        if not self.running:
            return

        self.scheduler.shutdown(wait=False)
        self.running = False
        print("⏰ 定时任务调度器已停止")

    async def _schedule_job(self, job: CronJob):
        """调度单个任务"""
        try:
            # 移除旧的任务（如果存在）
            if job.id in self.scheduler:
                self.scheduler.remove_job(job.id)

            # 如果任务未启用，不调度
            if not job.enabled:
                return

            # 解析 cron 表达式并添加任务
            trigger = CronTrigger.from_crontab(job.cron_expr)
            self.scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=job.id,
                args=[job.id]
            )
            print(f"✓ 任务已调度: {job.id} ({job.cron_expr})")
        except Exception as e:
            print(f"⚠️  调度任务失败 {job.id}: {e}")

    async def _execute_job(self, job_id: str):
        """执行任务（由调度器调用）"""
        job = self.store.get(job_id)
        if not job or not job.enabled:
            return

        print(f"⏰ 执行定时任务: {job.id} - {job.description}")
        error = None

        try:
            # 调用执行器
            await self.executor(job)
        except Exception as e:
            error = str(e)
            print(f"❌ 任务执行失败 {job.id}: {e}")

        # 更新执行状态
        self.store.mark_run(job_id, error)

    async def add_job(
        self,
        cron_expr: str,
        content: str,
        username: str,
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        tag: str = "task",
        description: str = ""
    ) -> str:
        """
        添加定时任务

        Returns:
            任务 ID
        """
        job_id = str(uuid.uuid4())[:8]

        job = CronJob(
            id=job_id,
            cron_expr=cron_expr,
            content=content,
            username=username,
            user_id=user_id,
            channel_id=channel_id,
            tag=tag,
            description=description or content[:50]
        )

        self.store.add(job)
        await self._schedule_job(job)

        print(f"✅ 已添加定时任务: {job_id}")
        return job_id

    def remove_job(self, job_id: str) -> bool:
        """删除定时任务"""
        if job_id in self.scheduler:
            self.scheduler.remove_job(job_id)

        return self.store.remove(job_id)

    def toggle_job(self, job_id: str, enabled: bool) -> bool:
        """启用/禁用任务"""
        job = self.store.get(job_id)
        if not job:
            return False

        job.enabled = enabled
        self.store.update(job)

        # 重新调度（需要在事件循环中）
        if self.running:
            asyncio.create_task(self._schedule_job(job))

        return True

    def list_jobs(self) -> List[CronJob]:
        """列出所有任务"""
        return self.store.list_all()

    def get_job(self, job_id: str) -> Optional[CronJob]:
        """获取任务详情"""
        return self.store.get(job_id)
