"""查看和修改定时任务配置。不负责到点执行。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import ScheduledTask
from shopbot.system.audit.service import audit
from shopbot.system.scheduler.schemas import ScheduledTaskUpdate, parse_model, parse_task_code


async def list_scheduled_tasks(session: AsyncSession) -> list[ScheduledTask]:
    result = await session.scalars(select(ScheduledTask).order_by(ScheduledTask.id).limit(100))
    return list(result.all())


@audit("scheduled_task.update")
@transactional
async def update_scheduled_task(
    session: AsyncSession,
    code: str,
    payload: ScheduledTaskUpdate | dict,
) -> ScheduledTask:
    task = await session.scalar(select(ScheduledTask).where(ScheduledTask.code == parse_task_code(code)))
    if task is None:
        raise BizException("定时任务不存在", code=BizCode.NOT_FOUND)
    data = parse_model(ScheduledTaskUpdate, payload)
    task.interval_seconds = data.interval_seconds
    task.is_active = data.is_active
    await session.flush()
    return task
