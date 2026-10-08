from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FastAPIProfile:
    name: str = "fastapi"
    required_files: frozenset[str] = frozenset({"main.py", "database.py", "requirements.txt"})
    canonical_entry_filenames: frozenset[str] = frozenset({"main.py", "database.py", "requirements.txt"})
    dependency_file: str = "requirements.txt"
    database_file: str = "database.py"
    endpoint_doc_file: str = "API_ENDPOINTS.md"
    entrypoint_file: str = "main.py"
    entrypoint_aliases: frozenset[str] = frozenset({"app.py"})
    framework_description: str = "FastAPI backend"
    route_wiring_label: str = "main.py"

    @property
    def required_files_text(self) -> str:
        return ", ".join(sorted(self.required_files))


FASTAPI_PROFILE = FastAPIProfile()
