# Fetch CLI

A Click-based CLI (`./fetch`) for automating back-office tasks. Python package lives in `src/`, entry point is `./fetch`.

## Project structure

```
fetch          # shell entry point (runs: uv run python -m src)
src/            # Python package
  __init__.py   # Click root group
  __main__.py   # CLI entry
  invoices/     # "invoices" subcommand group
config.yml      # runtime config (companies, destination folders, debtor accounts)
pyproject.toml  # dependencies + ruff config
```

## Running

```bash
./fetch -h
./fetch invoices fetch --max-emails 5
./fetch invoices to-dropbox
./fetch invoices generate-payments
```

## Gmail

When working **on this codebase**, route Gmail through the `gog gmail` CLI, not a
Gmail MCP connector, so calls go through the audited `helpers.async_run` wrapper
and `_safe_id` validation. This is a dev convention for the code, not a rule for
end users: a host agent (e.g. Cowork) may use its own Gmail connector for
search / read / draft / archive. The one job that still needs `gog` is landing an
attachment's bytes on disk (`gog gmail attachment`), since connectors return an
attachment id but not its content, and the rest of the pipeline reads real files.

```bash
gog gmail search "is:unread" --json
gog gmail get <messageId> --json
gog gmail thread get <threadId> --full --json
gog gmail attachment <messageId> <attachmentId> --out <dir> --name <filename>
gog gmail -h
```

## Linting & formatting

Ruff config is in `pyproject.toml`. Single quotes, spaces, Python 3.11+.

```bash
# Check for issues (dry run)
uvx ruff check src/

# Auto-fix what can be fixed
uvx ruff check --fix src/

# Format code
uvx ruff format src/
```

## Key conventions

- Config values (companies, destination folders, debtor accounts) live in `config.yml`, not hard-coded
- `DEFAULT_SLUG` in `config.yml` is the fallback company slug
- Classification uses `claude` CLI with haiku model (`classify.py`)
- Models are plain dataclasses in `models.py`
- All async subprocess calls go through `helpers.py` (`async_run`, `async_run_json`)
