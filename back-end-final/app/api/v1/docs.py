from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.models.user import User
from app.services.runtime_docs import API_ENDPOINTS_PATH, PROJECT_REQUIREMENTS_PATH

router = APIRouter(prefix="/docs", tags=["docs"])


class RuntimeDocOut(BaseModel):
    name: str
    path: str
    updated_at: datetime | None
    content: str


def _read_doc(name: str, path: Path) -> RuntimeDocOut:
    stat = path.stat() if path.exists() else None
    return RuntimeDocOut(
        name=name,
        path=path.name,
        updated_at=datetime.fromtimestamp(stat.st_mtime) if stat else None,
        content=path.read_text(encoding="utf-8") if path.exists() else "",
    )


@router.get("/api-endpoints", response_model=RuntimeDocOut)
async def api_endpoints(_: Annotated[User, Depends(get_current_user)]) -> RuntimeDocOut:
    return _read_doc("API Endpoints", API_ENDPOINTS_PATH)


@router.get("/project-requirements", response_model=RuntimeDocOut)
async def project_requirements(_: Annotated[User, Depends(get_current_user)]) -> RuntimeDocOut:
    return _read_doc("Project Requirements", PROJECT_REQUIREMENTS_PATH)
