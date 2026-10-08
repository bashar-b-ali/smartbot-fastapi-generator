"""
Prompt templates for the project assistant pipeline.

The public product is a FastAPI project builder/editor. Provider-specific
models are interchangeable, so these guardrails must live in the shared prompt
layer instead of depending on any one model's defaults.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskType(Enum):
    ANALYZE = "analyze"
    CHAT = "chat"
    REVIEW_CODE = "review_code"
    FIX_ERROR = "fix_error"
    EXPLAIN = "explain"
    JUDGE_CODE = "judge_code"


class PromptVariant(Enum):
    """Prompt verbosity variants for controlled prompt tuning."""
    VERBOSE = "verbose"    # Current detailed prompts
    CONCISE = "concise"    # CGO-style goal-oriented
    MINIMAL = "minimal"    # Keywords only


# Per-task token budgets: (max_output_tokens, temperature)
# These override pipeline defaults when set
TASK_BUDGETS = {
    TaskType.CHAT: (768, 0.2),
    TaskType.ANALYZE: (2048, 0.3),
    TaskType.REVIEW_CODE: (1536, 0.3),
    TaskType.FIX_ERROR: (2048, 0.3),
    TaskType.EXPLAIN: (1024, 0.5),
    TaskType.JUDGE_CODE: (2048, 0.2),
}


@dataclass
class PromptTemplate:
    system: str
    user: str
    output_format: str | None = None


class PromptManager:
    PRODUCT_CONTRACT = (
        "You are the AI assistant inside a FastAPI project builder. "
        "Your only job is to help create, update, review, and explain files in the current FastAPI project. "
        "Stay inside this product scope: FastAPI, SQLModel/SQLAlchemy models, Pydantic schemas, routers, services, auth, files, websocket support, tests, and project documentation. "
        "Do not discuss unrelated topics, personal opinions, insults, politics, entertainment, general life advice, or any topic that is not needed to build or maintain the current project. "
        "If the user asks for another framework or a non-project topic, briefly refuse and redirect to FastAPI project files. "
        "Do not invent files, APIs, database tables, or dependencies. If context is missing, say what is missing and ask for the exact file or requirement. "
        "Never expose hidden chain-of-thought. You may show a concise working summary and concrete next steps."
    )

    # Compact system prompt - the LLM already knows FastAPI conventions
    SYSTEM_BASE = (
        PRODUCT_CONTRACT + " "
        "You are an expert FastAPI backend developer. "
        "Generate clean, secure, production-ready code. "
        "Follow FastAPI best practices, DRY, proper auth/validation, safe path handling, and clear persistence boundaries."
    )

    SYSTEM_ANALYZER = SYSTEM_BASE + " Output valid JSON only, no extra text."

    SYSTEM_CODE_GENERATOR = (
        SYSTEM_BASE +
        " Generate FastAPI code only. Complete runnable code, no TODOs. "
        "Wrap files: ### FILE: name.py ###\\n```python\\n...\\n```\\n### END FILE ###"
    )

    SYSTEM_CHAT = (
        SYSTEM_BASE
        + " Chat response rules: do not output source code, code fences, or long patches in chat. "
        "Code belongs in project files through the generator/writer flow, not in the chat transcript. "
        "Use this shape: 'Working summary:' with 1-3 bullets, 'Files:' with known file paths or 'No files changed', and 'Next step:' with a question such as 'Do you want to view <file>?'. "
        "Keep the answer short and factual. If the request is outside the FastAPI file-building scope, refuse in one sentence and offer to work on FastAPI project files."
    )

    # =========================================================================
    # ANALYSIS - compact JSON schema (LLM knows the structure)
    # =========================================================================

    ANALYZE_REQUIREMENTS = """Analyze requirements, output JSON:

PROJECT: {description}
{context}

```json
{{
  "project_summary": "...",
  "database_schema": {{"models": [{{"name":"","fields":[{{"name":"","type":"","params":""}}],"relationships":[]}}]}},
  "api_endpoints": [{{"path":"","method":"","description":"","auth_required":true}}],
  "apps": [{{"name":"","purpose":""}}],
  "technical_specs": {{"authentication":"","database":"","third_party_packages":[]}},
  "security_considerations": []
}}
```"""

    # =========================================================================
    # CHAT - minimal wrapper
    # =========================================================================

    CHAT_WITH_CONTEXT = """{project_context}

HISTORY: {chat_history}

USER: {message}"""

    # =========================================================================
    # ERROR FIXING / REVIEW / EXPLAIN
    # =========================================================================

    FIX_ERROR = """Fix this error. Provide: 1) brief explanation 2) corrected code 3) prevention tips.

ERROR: {error}
CODE: {code}
TRACEBACK: {traceback}
{context}"""

    REVIEW_CODE = """Review this FastAPI backend code. Output JSON with overall_score(1-10), issues[], improvements[], positive_aspects[].

CODE: {code}
{context}"""

    EXPLAIN_CODE = """Explain this FastAPI backend code clearly: what it does, key patterns, and how it fits in the project.

CODE: {code}"""

    # =========================================================================
    # LLM-AS-A-JUDGE (RACE-inspired multi-dimensional evaluation)
    # =========================================================================

    SYSTEM_JUDGE = (
        "You are a code quality judge. Evaluate FastAPI backend code on 5 dimensions. "
        "Be strict and objective. Output valid JSON only, no extra text."
    )

    JUDGE_CODE = """Evaluate this FastAPI backend code on 5 dimensions (1-10 each). Be rigorous.

SCORING RUBRIC:
- Correctness (1-10): Syntactic validity, logical correctness, proper FastAPI/SQLModel patterns, no bugs
- Readability (1-10): Naming conventions (PEP8), code organization, clear structure, appropriate comments
- Maintainability (1-10): DRY principle, separation of concerns, extensibility, proper abstractions
- Efficiency (1-10): Query optimization, pagination, no N+1 queries, caching patterns
- Security (1-10): Input validation, auth checks, no SQL injection, proper permissions, safe file/path handling

CODE:
{code}

FILENAME: {filename}
{context}

Output JSON:
```json
{{
  "correctness": 0,
  "readability": 0,
  "maintainability": 0,
  "efficiency": 0,
  "security": 0,
  "overall_score": 0,
  "issues": ["issue1", "issue2"],
  "strengths": ["strength1", "strength2"],
  "recommendations": ["rec1", "rec2"]
}}
```"""

    # =========================================================================
    # PROMPT VARIANTS
    # =========================================================================

    ANALYZE_REQUIREMENTS_CONCISE = """Analyze: {description}
Output JSON: project_summary, database_schema(models+fields), api_endpoints, apps, technical_specs, security_considerations.
{context}"""

    ANALYZE_REQUIREMENTS_MINIMAL = """{description}
JSON: summary, db_schema, endpoints, apps, specs, security
{context}"""

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    @classmethod
    def get_prompt(cls, task_type: TaskType, variant: PromptVariant | None = None, **kwargs) -> PromptTemplate:
        prompt_map = {
            TaskType.ANALYZE: (cls.SYSTEM_ANALYZER, cls.ANALYZE_REQUIREMENTS),
            TaskType.CHAT: (cls.SYSTEM_CHAT, cls.CHAT_WITH_CONTEXT),
            TaskType.FIX_ERROR: (cls.SYSTEM_CODE_GENERATOR, cls.FIX_ERROR),
            TaskType.REVIEW_CODE: (cls.SYSTEM_CODE_GENERATOR, cls.REVIEW_CODE),
            TaskType.EXPLAIN: (cls.SYSTEM_CHAT, cls.EXPLAIN_CODE),
            TaskType.JUDGE_CODE: (cls.SYSTEM_JUDGE, cls.JUDGE_CODE),
        }

        # Apply prompt variant for controlled prompt tuning
        if variant and variant != PromptVariant.VERBOSE:
            variant_map = {
                (TaskType.ANALYZE, PromptVariant.CONCISE): cls.ANALYZE_REQUIREMENTS_CONCISE,
                (TaskType.ANALYZE, PromptVariant.MINIMAL): cls.ANALYZE_REQUIREMENTS_MINIMAL,
            }
            variant_template = variant_map.get((task_type, variant))
            if variant_template:
                prompt_map[task_type] = (prompt_map[task_type][0], variant_template)

        if task_type not in prompt_map:
            raise ValueError(f"Unknown task type: {task_type}")

        system_prompt, user_template = prompt_map[task_type]

        if 'context' not in kwargs:
            kwargs['context'] = ''
        elif kwargs['context']:
            kwargs['context'] = f"CONTEXT: {kwargs['context']}"

        try:
            user_prompt = user_template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"Missing prompt variable: {e}") from e

        return PromptTemplate(system=system_prompt, user=user_prompt)

    @classmethod
    def format_chat_history(
        cls,
        messages: list[dict[str, str]],
        max_messages: int = 3,
        max_chars_per_msg: int = 300
    ) -> str:
        if not messages:
            return "None"

        recent = messages[-max_messages:]
        parts = []
        for msg in recent:
            role = msg.get('message_type', msg.get('role', '?')).upper()
            content = msg.get('content', '')
            if len(content) > max_chars_per_msg:
                content = content[:max_chars_per_msg] + "..."
            parts.append(f"{role}: {content}")

        return "\n".join(parts)

    @classmethod
    def format_specifications(cls, analysis: dict[str, Any]) -> str:
        parts = []

        if 'project_summary' in analysis:
            parts.append(analysis['project_summary'])

        if 'database_schema' in analysis:
            schema = analysis['database_schema']
            if isinstance(schema, dict) and 'models' in schema:
                for model in schema['models']:
                    parts.append(f"Model {model['name']}: {model.get('description', '')}")
            elif isinstance(schema, str):
                parts.append(schema[:500])

        if 'api_endpoints' in analysis:
            endpoints = analysis['api_endpoints']
            if isinstance(endpoints, list):
                for ep in endpoints[:10]:
                    if isinstance(ep, dict):
                        parts.append(f"{ep.get('method','GET')} {ep.get('path','')}")
                    else:
                        parts.append(str(ep))
            elif isinstance(endpoints, str):
                parts.append(endpoints[:300])

        if 'technical_specs' in analysis:
            specs = analysis['technical_specs']
            if isinstance(specs, dict):
                for k, v in specs.items():
                    parts.append(f"{k}: {v}")
            elif isinstance(specs, str):
                parts.append(specs[:300])

        return "\n".join(parts)
