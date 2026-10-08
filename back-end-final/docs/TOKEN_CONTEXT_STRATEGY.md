# Token Context Strategy

Token reduction must not mean starving the model of project context. The system should reduce tokens by sending a compact, searchable project map first, then expanding to exact files only when the task requires implementation detail.

## Better Default

Use a persistent project index as the main prompt context:

- API routes: method, path, handler, source line, request parameters, body schemas, response model, status code.
- Data models and schemas: class name, source file, table name, bases, fields, primary keys, foreign keys, indexes, nullability.
- Functions: signature, source line, route binding, summary, important body symbols, query filters.
- Files: path, size, hash, imports, detected routes, detected tables.
- Accepted requirement contracts: previous prompts, covered artifacts, missing/partial notes, changed files.
- Recent changes: only as operational memory, not as a substitute for the project index.

This gives the model enough structure to choose the right files and avoid reading entire files for every request.

## Expansion Rule

The model should receive full source only for targeted files selected by the index. Selection should prefer:

- Explicit paths mentioned by the user.
- Files containing matching tables, classes, route handlers, filters, or imported symbols.
- Related router/model/schema files needed for a coherent edit.
- Entrypoint wiring files when routes or routers change.

## What Not To Do

Avoid token savings based mainly on arbitrary limits such as "last three messages", "only five functions", or a tiny project summary. Those limits make code generation worse because they remove the exact context that prevents hallucinated files, duplicate handlers, broken imports, and missing fields.

## Success Metric

A good context strategy should reduce full-file prompt size while preserving edit quality:

- Higher validation pass rate.
- Fewer no-change or wrong-file edits.
- Fewer missing artifacts and regressions.
- Lower input tokens compared with sending the whole project.
- Stable output tokens based on task type, not one global max.