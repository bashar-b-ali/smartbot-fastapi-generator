"""
Response Parsers for LLM Output
Extracts structured data from LLM responses (JSON, code blocks, etc.)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedCode:
    """Represents a parsed code block"""
    filename: str
    content: str
    language: str = "python"
    start_line: int = 0
    end_line: int = 0


@dataclass
class ParsedAnalysis:
    """Represents parsed project analysis"""
    project_summary: str = ""
    database_schema: dict[str, Any] = field(default_factory=dict)
    api_endpoints: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, str]] = field(default_factory=list)
    technical_specs: dict[str, Any] = field(default_factory=dict)
    security_considerations: list[str] = field(default_factory=list)
    raw_json: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_summary": self.project_summary,
            "database_schema": self.database_schema,
            "api_endpoints": self.api_endpoints,
            "apps": self.apps,
            "technical_specs": self.technical_specs,
            "security_considerations": self.security_considerations,
        }


@dataclass
class ParsedCodeGeneration:
    """Represents parsed code generation response"""
    files: list[ParsedCode] = field(default_factory=list)
    explanation: str = ""
    dependencies: list[str] = field(default_factory=list)

    def get_file(self, filename: str) -> ParsedCode | None:
        """Get a specific file by name"""
        for f in self.files:
            if f.filename == filename or f.filename.endswith(filename):
                return f
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [{"filename": f.filename, "content": f.content, "language": f.language} for f in self.files],
            "explanation": self.explanation,
            "dependencies": self.dependencies,
        }


class ResponseParser:
    """Parses LLM responses into structured data"""

    # Regex patterns for extraction
    JSON_BLOCK_PATTERN = r'```(?:json)?\s*\n?([\s\S]*?)\n?```'
    CODE_BLOCK_PATTERN = r'```(\w*)\s*\n([\s\S]*?)\n```'
    FILE_MARKER_PATTERN = r'###\s*FILE:\s*([^\s#]+)\s*###'
    FILE_END_PATTERN = r'###\s*END\s*FILE\s*###'
    PYTHON_FILENAME_COMMENT = r'#\s*(?:File:\s*)?(\w+\.py)'

    @classmethod
    def parse_json(cls, response: str) -> dict[str, Any] | None:
        """
        Extract and parse JSON from LLM response.
        Handles JSON in code blocks or raw JSON.
        """
        # Try to find JSON in code blocks first
        json_matches = re.findall(cls.JSON_BLOCK_PATTERN, response, re.MULTILINE)

        for match in json_matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        # Try parsing the whole response as JSON
        try:
            # Find JSON object boundaries
            start = response.find('{')
            end = response.rfind('}') + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        # Try to find JSON array
        try:
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        logger.warning("Could not parse JSON from response")
        return None

    @classmethod
    def parse_analysis(cls, response: str) -> ParsedAnalysis:
        """Parse project analysis response into structured data"""
        analysis = ParsedAnalysis()

        json_data = cls.parse_json(response)

        if json_data:
            analysis.raw_json = json_data
            analysis.project_summary = json_data.get('project_summary', '')
            analysis.database_schema = json_data.get('database_schema', {})
            analysis.api_endpoints = json_data.get('api_endpoints', [])
            analysis.apps = json_data.get('apps', [])
            analysis.technical_specs = json_data.get('technical_specs', {})
            analysis.security_considerations = json_data.get('security_considerations', [])
        else:
            # Fallback: Try to parse sections manually
            analysis = cls._parse_analysis_sections(response)

        return analysis

    @classmethod
    def _parse_analysis_sections(cls, response: str) -> ParsedAnalysis:
        """Fallback parser for non-JSON analysis responses"""
        analysis = ParsedAnalysis()

        # Try to extract sections by headers
        sections = {
            'database_schema': '',
            'api_endpoints': '',
            'technical_specs': '',
        }

        current_section = None
        lines = response.split('\n')

        for line in lines:
            line_lower = line.lower().strip()

            if 'database' in line_lower and ('schema' in line_lower or 'model' in line_lower):
                current_section = 'database_schema'
            elif 'api' in line_lower and 'endpoint' in line_lower:
                current_section = 'api_endpoints'
            elif 'technical' in line_lower or 'spec' in line_lower:
                current_section = 'technical_specs'
            elif 'summary' in line_lower:
                current_section = 'summary'
            elif current_section and line.strip():
                if current_section == 'summary':
                    analysis.project_summary += line + '\n'
                else:
                    sections[current_section] += line + '\n'

        # Process api_endpoints into list
        if sections['api_endpoints']:
            endpoints = []
            for line in sections['api_endpoints'].split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('*'):
                    endpoints.append(line[1:].strip())
                elif line:
                    endpoints.append(line)
            analysis.api_endpoints = endpoints

        # Store raw sections for database schema and tech specs
        if sections['database_schema']:
            analysis.database_schema = {'raw': sections['database_schema']}

        if sections['technical_specs']:
            analysis.technical_specs = {'raw': sections['technical_specs']}

        return analysis

    @classmethod
    def parse_code_blocks(cls, response: str) -> list[ParsedCode]:
        """
        Extract all code blocks from response.
        Tries to determine filenames from context.
        """
        code_blocks = []
        block_matches = re.finditer(cls.CODE_BLOCK_PATTERN, response, re.MULTILINE)

        # Track position for filename context
        last_pos = 0

        for i, match in enumerate(block_matches):
            language = match.group(1) or 'python'
            content = match.group(2).strip()

            # Try to find filename
            filename = cls._extract_filename(response, match.start(), last_pos, i, language)

            code_blocks.append(ParsedCode(
                filename=filename,
                content=content,
                language=language,
                start_line=response[:match.start()].count('\n') + 1,
                end_line=response[:match.end()].count('\n') + 1
            ))

            last_pos = match.end()

        return code_blocks

    @classmethod
    def _extract_filename(
        cls,
        response: str,
        block_start: int,
        last_pos: int,
        index: int,
        language: str
    ) -> str:
        """Try to extract filename from context around code block"""

        # Check for FILE marker before this block
        context_before = response[last_pos:block_start]

        # Look for ### FILE: filename.py ### pattern
        file_match = re.search(cls.FILE_MARKER_PATTERN, context_before)
        if file_match:
            return file_match.group(1)

        # Look for "# filename.py" comment pattern
        file_comment = re.search(cls.PYTHON_FILENAME_COMMENT, context_before)
        if file_comment:
            return file_comment.group(1)

        # Look for "filename.py" or "filename:" in the line before
        lines_before = context_before.strip().split('\n')
        if lines_before:
            last_line = lines_before[-1].lower()
            py_file = re.search(r'(\w+\.py)', last_line)
            if py_file:
                return py_file.group(1)

        # Default filename based on content analysis
        return cls._infer_filename_from_content(response[block_start:], language, index)

    @classmethod
    def _infer_filename_from_content(cls, content: str, language: str, index: int) -> str:
        """Infer filename from code content"""
        content_lower = content.lower()

        if language == 'python' or language == '':
            if 'fastapi' in content_lower and 'apirouter' in content_lower:
                return 'routers/generated.py'
            if 'fastapi' in content_lower and 'fastapi(' in content_lower:
                return 'main.py'
            if 'sqlmodel' in content_lower or 'sqlalchemy' in content_lower:
                return 'models.py'
            if 'basemodel' in content_lower:
                return 'schemas.py'
            if 'include_router' in content_lower:
                return 'main.py'
            if 'testcase' in content_lower or 'def test_' in content_lower:
                return 'tests.py'

        # Fallback with index
        ext = {'python': 'py', 'javascript': 'js', 'html': 'html', 'css': 'css'}.get(language, 'txt')
        return f'code_{index + 1}.{ext}'

    @classmethod
    def parse_code_generation(cls, response: str) -> ParsedCodeGeneration:
        """
        Parse a code generation response that may contain multiple files.
        """
        result = ParsedCodeGeneration()

        # First, try to parse using FILE markers
        if '### FILE:' in response:
            result.files = cls._parse_marked_files(response)
        else:
            # Fall back to code block extraction
            result.files = cls.parse_code_blocks(response)

        # Deduplicate files by filename, keeping the longest version
        seen = {}
        for f in result.files:
            if f.filename not in seen or len(f.content) > len(seen[f.filename].content):
                seen[f.filename] = f
        result.files = list(seen.values())

        # Extract any text outside code blocks as explanation
        explanation_text = re.sub(cls.CODE_BLOCK_PATTERN, '', response)
        explanation_text = re.sub(cls.FILE_MARKER_PATTERN, '', explanation_text)
        explanation_text = re.sub(cls.FILE_END_PATTERN, '', explanation_text)
        result.explanation = explanation_text.strip()

        # Try to find dependencies
        deps_pattern = r'(?:pip install|requirements?:?)\s+([^\n]+)'
        deps_matches = re.findall(deps_pattern, response, re.IGNORECASE)
        for match in deps_matches:
            packages = re.findall(r'[\w-]+(?:==[\d.]+)?', match)
            result.dependencies.extend(packages)

        return result

    @classmethod
    def _parse_marked_files(cls, response: str) -> list[ParsedCode]:
        """Parse files marked with ### FILE: filename ### markers"""
        files = []

        # Split by file markers
        parts = re.split(cls.FILE_MARKER_PATTERN, response)

        # parts[0] is before first marker, then alternating: filename, content, filename, content...
        for i in range(1, len(parts), 2):
            if i + 1 < len(parts):
                filename = parts[i].strip()
                content = parts[i + 1]

                # Remove END FILE marker
                content = re.sub(cls.FILE_END_PATTERN, '', content).strip()

                # Extract code from code blocks if present
                code_match = re.search(cls.CODE_BLOCK_PATTERN, content)
                if code_match:
                    content = code_match.group(2).strip()

                if content:
                    files.append(ParsedCode(
                        filename=filename,
                        content=content,
                        language='python' if filename.endswith('.py') else 'text'
                    ))

        return files

    @classmethod
    def extract_sql_schema(cls, response: str) -> str:
        """Extract SQL schema from response"""
        # Look for SQL code blocks
        sql_pattern = r'```sql\s*\n([\s\S]*?)\n```'
        matches = re.findall(sql_pattern, response, re.IGNORECASE)

        if matches:
            return '\n\n'.join(matches)

        # Look for CREATE TABLE statements
        create_pattern = r'(CREATE\s+TABLE[\s\S]*?;)'
        creates = re.findall(create_pattern, response, re.IGNORECASE)

        if creates:
            return '\n\n'.join(creates)

        return ""

    @classmethod
    def parse_error_fix(cls, response: str) -> dict[str, Any]:
        """Parse error fix response"""
        result = {
            'explanation': '',
            'fixed_code': '',
            'recommendations': []
        }

        # Extract code
        code_blocks = cls.parse_code_blocks(response)
        if code_blocks:
            result['fixed_code'] = code_blocks[0].content

        # Extract explanation (text before first code block)
        if '```' in response:
            result['explanation'] = response[:response.find('```')].strip()
        else:
            result['explanation'] = response

        # Extract recommendations
        rec_pattern = r'(?:recommend|suggest|tip|note)[\s:]+([^\n]+)'
        recs = re.findall(rec_pattern, response, re.IGNORECASE)
        result['recommendations'] = recs

        return result

    @classmethod
    def parse_code_review(cls, response: str) -> dict[str, Any]:
        """Parse code review response"""
        # Try JSON first
        json_data = cls.parse_json(response)
        if json_data:
            return json_data

        # Manual parsing fallback
        result = {
            'overall_score': 0,
            'issues': [],
            'improvements': [],
            'positive_aspects': []
        }

        # Try to find score
        score_match = re.search(r'score[:\s]+(\d+)', response, re.IGNORECASE)
        if score_match:
            result['overall_score'] = int(score_match.group(1))

        # Extract issues
        if 'issue' in response.lower():
            issue_section = response[response.lower().find('issue'):]
            issues = re.findall(r'-\s+([^\n]+)', issue_section[:500])
            result['issues'] = [{'description': i} for i in issues[:10]]

        return result

    @classmethod
    def clean_code(cls, code: str) -> str:
        """Clean up generated code"""
        # Remove leading/trailing whitespace
        code = code.strip()

        # Remove markdown artifacts
        code = re.sub(r'^```\w*\s*', '', code)
        code = re.sub(r'\s*```$', '', code)

        # Normalize line endings
        code = code.replace('\r\n', '\n')

        # Remove excessive blank lines (more than 2 consecutive)
        code = re.sub(r'\n{3,}', '\n\n', code)

        return code
