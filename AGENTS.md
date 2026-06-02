# Repository Guidelines

## Project Structure & Module Organization

This repository is a small Python MCP server for accessing an Obsidian LiveSync vault through CouchDB. Core files live at the root:

- `server.py`: FastMCP server setup, tool definitions, OAuth wiring, and environment-driven configuration.
- `couchdb.py`: CouchDB client and note/document access logic.
- `oauth_provider.py`: OAuth 2.1 authorization provider used when OAuth is enabled.
- `run.py`: local entry point for starting the server.
- `requirements.txt`: runtime Python dependencies.
- `.env.example`: required configuration template.
- `obsidian-mcp.service`: optional systemd deployment unit.

There is no `tests/` directory or packaged source tree yet. Keep new modules focused and colocated unless the project grows enough to justify a package layout.

## Build, Test, and Development Commands

Create a virtual environment before local work:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell, activate with `.\venv\Scripts\Activate.ps1`.

Run the server locally:

```bash
python run.py
```

Copy `.env.example` to `.env` and set CouchDB, OAuth, and MCP host values before running against real data.

## Coding Style & Naming Conventions

Use Python 3.11+ and PEP 8 conventions: 4-space indentation, clear names, and snake_case for variables, functions, and helpers. Keep MCP tool handlers descriptive and aligned with existing names such as `create_note`, `manage_tags`, and `vault_stats`. Prefer typed function signatures where practical.

No formatter or linter config is committed. If adding one, document the command here and avoid reformatting unrelated files.

## Testing Guidelines

No automated test suite is present. For changes to note parsing, CouchDB document handling, OAuth, or MCP tools, add focused tests before broad refactors. Use `tests/test_<module>.py` with pytest-style functions, for example `tests/test_couchdb.py`.

Until tests exist, verify manually with a configured CouchDB instance and `python run.py`. Exercise the changed MCP tool and check note content, frontmatter, and CouchDB revisions.

## Commit & Pull Request Guidelines

Recent commits use short, imperative subjects with Conventional Commit prefixes, for example `docs: add CHANGELOG` and `fix: prevent silent data loss in search-replace on chunked documents`. Prefer `docs:`, `fix:`, `feat:`, or `chore:`.

Pull requests should include purpose, affected files or tool paths, verification performed, and configuration or migration notes. Link related issues when available.

## Security & Configuration Tips

Never commit `.env`, CouchDB credentials, OAuth client secrets, or public deployment tokens. When changing OAuth behavior, document client registration assumptions and redirect URI implications in `README.md`.
