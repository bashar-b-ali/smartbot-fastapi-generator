from fastapi import APIRouter

from app.api.v1 import auth, chatbot, docs, llm_models, project_runs, projects, users, ws

api_router = APIRouter(prefix="/api/v1")


@api_router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(projects.router)
api_router.include_router(project_runs.router)
api_router.include_router(chatbot.router)
api_router.include_router(docs.router)
api_router.include_router(llm_models.router)
api_router.include_router(ws.router)
