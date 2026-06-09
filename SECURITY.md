# Security

Fetch is a local MCP server. Your own AI agent runs it on your machine, under your
own logins. This page states its security posture plainly, with pointers to the
code so an agent (or you) can verify each claim. The test suite checks the main
defenses (`tests/test_security.py`, `tests/test_gmail.py`).

## Trust model

Fetch holds nothing of its own. It shells out to two CLIs that you have already
signed in: `gog` for Gmail and `claude` for PDF classification. It is exactly as
trusted as the agent and the accounts you point it at. It is not a service, it has
no server you log into, and your invoice data does not flow through anyone else.

## What it does not do

- **It never sends email.** The only email tool is `draft_reply`, which calls
  `gog gmail drafts create` (`src/invoices/gmail.py`, `create_draft_reply`). There
  is no send path anywhere in the code. You review every draft, its recipient, and
  its attachments in Gmail, and you click send.
- **It stores no credentials.** No passwords, tokens, or API keys are read or
  written. Gmail goes through your `gog` session; classification through your
  `claude` CLI. `config.yml` holds your company names, destination folders (any
  path you choose), and beneficiary bank details (not logins) and is gitignored. Outputs are
  `invoices.json`, `SUMMARY.md`, `REPORT.md`, and `audit.log`; none contain secrets.
- **It barely touches the network itself.** The only outbound request the Python
  code makes is a currency-rate lookup (`src/invoices/helpers.py`,
  `fetch_exchange_rate`) to `hexarate.paikama.co`, sending only a three-letter
  currency pair (for example `USD/EUR`) and no amounts, names, or other data. It
  fails gracefully if offline. All Gmail and Anthropic traffic is made by your
  `gog` and `claude` CLIs, not by Fetch.

## Hardening

- **Untrusted email content is fenced.** Email subject, sender, snippet, and thread
  text are wrapped in `UNTRUSTED_EMAIL_DATA` markers before the classifier sees
  them (`src/invoices/classify.py`).
- **The classifier is least-privilege.** It runs with `--allowedTools Read` and
  **without** `--permission-mode bypassPermissions`, so injected instructions in a
  PDF or email cannot reach Bash, Write, or any other tool. The missing-list parser
  likewise never uses `bypassPermissions` and fences its input as data
  (`src/invoices/reconcile.py`).
- **No path traversal.** Attachment filenames come from the email (attacker
  influenced), so they are reduced to a bare basename and namespaced under the
  message id before writing (`download_attachment`).
- **No argument injection.** Gmail message, thread, and attachment ids are
  validated (`_safe_id`) so a crafted value like `-x` cannot be read as a CLI flag
  by `gog`.
- **No shell.** Subprocesses run via `asyncio.create_subprocess_exec` with list
  arguments and `stdin=DEVNULL` (`src/invoices/helpers.py`, `async_run`).
- **Local audit log.** Every fetch, download, and draft is recorded in
  `output/audit.log` (`src/invoices/audit.py`).

## Capability boundary (read this)

Fetch gives your agent real abilities, under your accounts: it can search and read
your Gmail, download attachments, classify a PDF you point it at, write files under
the output directory, and create Gmail drafts.

Two tools take a local file path chosen by the agent: `classify_invoice(pdf_path)`
reads that file (via your `claude` CLI) to classify it, and
`draft_reply(attachments=[...])` attaches the listed files to a draft. Fetch does
not restrict which local paths your agent may name. So if your agent were hijacked
by a prompt injection from somewhere upstream, it could in principle read a local
file through `classify_invoice` or attach one to a draft.

That blast radius is bounded by the guarantees above: nothing is ever sent
automatically (you review every draft, recipient, and attachment before sending),
no credentials are stored, no shell runs, and the classifier can only read. Treat
Fetch like any capable local tool your agent runs: trust it as much as you trust
the agent and the accounts you give it.

## Verify it yourself

```bash
uv run pytest        # runs the security tests too
```

Key code to read: `create_draft_reply` and `_safe_id` and `download_attachment`
(`gmail.py`), `classify_pdf` (`classify.py`), `fetch_exchange_rate` (`helpers.py`),
`async_run` (`helpers.py`), `audit.py`.

## Reporting a vulnerability

Please open a private GitHub security advisory on this repository, or open an issue
if you cannot. We will respond as quickly as we can.
