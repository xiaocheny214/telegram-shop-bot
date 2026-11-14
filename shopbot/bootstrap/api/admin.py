"""管理接口。这个路由才读取角色注解。支付回调和健康检查不挂这里。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.bootstrap.deps import get_session
from shopbot.system.access.guard import enforce_access


async def access_guard(request: Request, session: AsyncSession = Depends(get_session)) -> None:
    await enforce_access(request, session)


router = APIRouter(dependencies=[Depends(access_guard)])
