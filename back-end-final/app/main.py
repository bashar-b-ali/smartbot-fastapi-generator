import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import add_request_id_middleware, register_exception_handlers
from app.core.logging import configure_logging, logger
from app.db.session import SessionLocal, engine
from app.realtime.manager import manager as realtime_manager
from app.repositories.chat import ChatMessageRepository
from app.services.ollama_runtime import warm_server_ollama_model
from app.services.project_activity import clear_orphaned_project_activity
from app.services.project_runs import project_run_worker
from app.services.runtime_docs import write_runtime_docs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    def _connection_reset_handler(active_loop, context):
        error = context.get("exception")
        if isinstance(error, ConnectionResetError) and getattr(error, "winerror", None) == 10054:
            return
        if previous_exception_handler:
            previous_exception_handler(active_loop, context)
        else:
            active_loop.default_exception_handler(context)
    loop.set_exception_handler(_connection_reset_handler)
    logger.info("startup", env=settings.environment, debug=settings.debug)
    if not settings.is_production:
        async with SessionLocal() as db:
            cleared_activity = await clear_orphaned_project_activity(db)
            interrupted_messages = await ChatMessageRepository(db).mark_interrupted_pending_generation_messages()
            await db.commit()
            if cleared_activity or interrupted_messages:
                logger.warning(
                    "startup.cleared_orphaned_generation_state",
                    activity_runs=cleared_activity,
                    pending_messages=interrupted_messages,
                )
    await warm_server_ollama_model()
    await realtime_manager.start()
    await project_run_worker.start()
    try:
        yield
    finally:
        loop.set_exception_handler(previous_exception_handler)
        await project_run_worker.stop()
        await realtime_manager.stop()
        await engine.dispose()
        logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )

    local_dev_origin_regex = None
    if not settings.is_production:
        local_dev_origin_regex = r"https?://(localhost|127\.0\.0\.1):[0-9]+"

    if settings.cors_origins or local_dev_origin_regex:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_origin_regex=local_dev_origin_regex,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    add_request_id_middleware(app)
    register_exception_handlers(app)
    app.include_router(api_router)
    write_runtime_docs(app)
    return app


app = create_app()
