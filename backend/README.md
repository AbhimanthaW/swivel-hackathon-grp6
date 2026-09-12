# Backend implementation belongs here

This directory intentionally contains **no implementation** and no data.

Start with:
1. `../docs/BACKEND-HANDOFF.md` — pipeline, lifecycle and persistence semantics.
2. `../contracts/openapi.json` — proposed public HTTP contract.
3. `../contracts/llm-structuring.schema.json` — proposed internal LLM output boundary.
4. `../docs/ACCEPTANCE.md` — integration acceptance checks.

Choose the Python framework, CSV libraries, model provider and database as a team. Serve the contract at `/api/v1`, normally on port 8001 for local development. The frontend dev proxy preserves this prefix.

Do not implement a fake analysis endpoint to make the UI look populated. Empty responses are appropriate only when they reflect an actual empty persisted workspace. No LLM keys or raw exports belong in Git.
