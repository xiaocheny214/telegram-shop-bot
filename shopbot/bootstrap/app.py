"""FastAPI 应用工厂。装配异常处理和 API 路由。"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shopbot.bootstrap.api import router
from shopbot.bootstrap.bot import start_bots, stop_bots
from shopbot.bootstrap.handler import register_exception_handlers
from shopbot.framework.config.database import get_database_settings
from shopbot.framework.db import create_session_factory, init_db
from shopbot.framework.executor import start_executor, stop_executor
from shopbot.shop.order.service import close_expired_orders
from shopbot.system.audit.service import bind_audit_factory, configure_audit_logging, start_audit_writer, stop_audit_writer
from shopbot.system.scheduler.launcher import TaskSpec, register, run_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_executor()
    engine = await init_db(get_database_settings())
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    bind_audit_factory(app.state.session_factory)
    start_audit_writer()
    register(TaskSpec("close_expired_orders", "关闭超时未支付订单", 5), close_expired_orders)
    scheduler = asyncio.create_task(run_scheduler(app.state.session_factory))
    bots: list = []
    try:
        bots = await start_bots(app.state.session_factory)
        yield
    finally:
        await stop_bots(bots)
        scheduler.cancel()
        try:
            await scheduler
        except asyncio.CancelledError:
            pass
        await stop_audit_writer()
        stop_executor()
        await engine.dispose()


def create_app() -> FastAPI:
    configure_audit_logging()
    app = FastAPI(title="Telegram 卡密商店", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    from shopbot.framework.config.app import get_app_settings

    settings = get_app_settings()
    uvicorn.run(
        "shopbot.bootstrap.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
