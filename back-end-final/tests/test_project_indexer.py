from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.project_indexer import ProjectIndexer


class _DB:
    async def flush(self) -> None:
        return None


class _Helpers:
    def __init__(self) -> None:
        self.helper = SimpleNamespace(
            database_schema={},
            api_routes=[],
            file_index=[],
            function_summaries=[],
            requirement_contracts=[],
            project_context="",
            recent_changes=[],
        )

    async def get_or_create(self, _project_id):
        return self.helper


@pytest.mark.asyncio
async def test_pre_edit_refresh_can_index_without_writing_endpoint_docs(tmp_path) -> None:
    project = SimpleNamespace(
        id=uuid4(),
        folder_path=str(tmp_path),
        is_active=True,
        name="Empty project",
        description="",
    )
    indexer = ProjectIndexer(_DB())
    indexer.helpers = _Helpers()

    result = await indexer.refresh(project, reason="pre_edit", write_endpoint_doc=False)

    assert not (tmp_path / "API_ENDPOINTS.md").exists()
    assert result["endpoint_doc"]["write_skipped"] is True
    assert result["file_index"] == []
