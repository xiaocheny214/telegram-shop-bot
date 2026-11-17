"""请求级数据库会话。一个请求一个事务。"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import begin_immediate
from shopbot.system.audit.context import bind_request, reset_request
from shopbot.system.audit.service import flush_audits


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    client = request.client
    token = bind_request(request.url.path, None if client is None else client.host)
    try:
        async with request.app.state.session_factory() as session:
            try:
                async with begin_immediate(session):
                    yield session
            finally:
                await flush_audits()
    finally:
        reset_request(token)
