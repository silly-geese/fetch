# Fetch

Turn an accountant's "missing invoices" list into a ready-to-send reply with the files attached.

Fetch is a local toolkit your own AI agent drives. It runs on your machine, under your own logins, and keeps no credentials of its own.

## Who it is for

Small companies (and their agents) that get a periodic "please send the invoices we are missing" message from their accountant, and want to answer it fast.

## What it does

1. Reads the accountant's list, in any format (pasted text, a table, an email).
2. Finds each invoice. First in your email (one mailbox or several, Gmail or IMAP). For anything not there, it hands your agent a clear task to fetch it from the vendor's portal or e-invoice platform.
3. Drafts a reply to the accountant with the found PDFs attached, plus a note on anything still missing. You review the draft and send it.

It can also classify and file invoices, copy them to a folder of your choice (Dropbox, a shared drive, or any local folder), and build a SEPA payment file. See the tool list below.

## How it stays safe

- No stored credentials. It uses your local `gog` (Gmail) and `claude` CLIs. IMAP passwords come from environment variables, never from config.
- It drafts, it does not send. Outgoing replies are Gmail drafts you review first.
- Local audit log. Every fetch, download, and draft is recorded in `output/audit.log`.

Full posture, hardening, and the honest capability boundary: [SECURITY.md](SECURITY.md).

## Quick start: hand it to your agent

Fetch is an MCP server, and the easy way to set it up and use it is to paste the
block below to your MCP-capable AI agent (such as Claude Code). There is no clone
or install step: `uvx` runs Fetch straight from this repo.

You need [uv](https://docs.astral.sh/uv/), plus your own
[gog](https://github.com/openclaw/gogcli) (Gmail, `brew install openclaw/tap/gogcli`) and
[claude](https://docs.anthropic.com/en/docs/claude-code) CLIs. The agent can check
those for you and help you install them.

```text
Set up the "fetch" invoice toolkit for me, then help me use it.

1. Add an MCP server named "fetch-invoices" to your config: command uvx, args --from git+https://github.com/silly-geese/fetch fetch-mcp. Set env FETCH_CONFIG to a new file ~/fetch/config.yml and FETCH_OUTPUT_DIR to ~/fetch/output (use absolute paths).
2. Create ~/fetch/config.yml from the template at https://github.com/silly-geese/fetch/blob/main/config.example.yml, and ask me for my company names, the folder where each company's invoices should be filed (any folder, e.g. a Dropbox or shared-drive path), and bank (debtor) details to fill it in.
3. Call health_check and confirm gog and claude are installed and signed in. If not, walk me through it.

From then on, when I forward you a list of missing invoices: call parse_missing_list, then find each one in my Gmail with search_inbox, get_message, and download_attachment. For anything not in the inbox, call plan_retrieval and fetch it with your own browser. Finally draft_reply to the accountant with the files attached and a short note on anything still missing. Always leave a draft for me to review; never send.
```

The MCP entry it adds looks like this:

```jsonc
{
  "mcpServers": {
    "fetch-invoices": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/silly-geese/fetch", "fetch-mcp"],
      "env": {
        "FETCH_CONFIG": "/absolute/path/to/your/config.yml",
        "FETCH_OUTPUT_DIR": "/absolute/path/to/your/output"
      }
    }
  }
}
```

## Manual setup (optional)

Prefer to run it yourself or use the CLI? Clone it and point the server at the clone:

```bash
git clone https://github.com/silly-geese/fetch.git
cd fetch
cp config.example.yml config.yml   # fill in your details
uv sync
./fetch onboarding                 # checks your setup
```

```jsonc
{
  "mcpServers": {
    "fetch-invoices": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/your/clone", "fetch-mcp"]
    }
  }
}
```

`./fetch mcp` runs the same server.

### Tools

| Tool | What it does |
|------|--------------|
| `health_check` | Check gog, claude, Gmail auth, and config.yml |
| `parse_missing_list` | Turn a free-form missing list into a checklist |
| `search_inbox`, `get_message`, `download_attachment` | Find and pull invoice PDFs from your mailboxes |
| `plan_retrieval` | Build tasks to fetch invoices that are not in the inbox |
| `draft_reply` | Draft a reply to the accountant with files attached (never sends) |
| `read_audit` | Read the local audit log |
| `reconcile`, `build_report` | Optional: match the list to what you found and write a summary |
| `fetch_invoices`, `classify_invoice`, `list_invoices` | Fetch and classify invoices from all your mailboxes |
| `copy_to_dropbox`, `generate_payments`, `archive_thread` | File to a folder (Dropbox or any path), build SEPA payments, archive threads |

Full reference and the step-by-step workflow: [SKILL.md](SKILL.md).

## Connecting Gmail

Fetch reads Gmail through your local `gog` CLI, so the server itself never holds
your credentials. There are two ways to give `gog` access.

**Standard, best for hands-off use.** Create a Google OAuth client once, then sign
in. This stores a refresh token and keeps working without you:

1. In Google Cloud Console: create a project, enable the Gmail API, create an
   OAuth client of type "Desktop app", and download the client secret JSON.
2. Register it and sign in (a browser opens for consent):

   ```bash
   gog auth credentials <client_secret.json>
   gog auth add you@example.com --services gmail
   gog auth doctor --check
   ```

**Quick, no Cloud project.** If you already have a Gmail access token, `gog` can
use it directly with no OAuth client setup at all:

```bash
export GOG_ACCESS_TOKEN=<a gmail.modify-scoped token>
```

The token needs the `https://www.googleapis.com/auth/gmail.modify` scope (read
alone is enough to search and download, but drafting and archiving need modify).
Access tokens last about an hour and are not refreshed on this path, so it suits
a single session rather than a long unattended run. The easiest way to mint a
token without your own Cloud project is Google's OAuth Playground.

Either way, `health_check` tells you whether Gmail is connected.

## Multiple email accounts

By default Fetch searches the Gmail account `gog` is signed in to. To search several mailboxes in one run, list them under `email_accounts` in `config.yml`:

```yaml
email_accounts:
  # Google-hosted mailboxes (gmail.com or any Workspace domain)
  - address: you@example.com
  - address: info@other-domain.com

  # Any other mailbox via IMAP
  - address: user@elsewhere.com
    provider: imap
    imap:
      host: imap.elsewhere.com
      port: 993                       # optional, default 993
      username: user@elsewhere.com    # optional, defaults to address
      password_env: ELSEWHERE_IMAP_PASSWORD
      folder: INBOX                   # optional
      archive_folder: Archive         # optional, used when archiving
```

Google accounts need `gog auth add <address>` once each. The domain does not matter, only that the mailbox is hosted on Google. When accounts span more than one Workspace domain, create the OAuth client as "External" and add each address as a test user while the app is unverified.

IMAP passwords are never stored in `config.yml`. Set `password_env` to the name of an environment variable and export it before running. For providers with two-factor auth, use an app password.

Searches cover all accounts at once, and one failing account does not stop the others. Message and thread ids are scoped to the account they came from, so the MCP tools take an optional `account` parameter (only needed when several accounts are configured). Draft replies always go through a Google account.

`./fetch onboarding` (or the `health_check` tool) checks all of this: gog auth for each Google account, whether each Gmail-configured domain is actually Google-hosted, and IMAP connectivity.

## Use it inside Cowork (or any agent that already has Gmail)

If your agent already has its own Gmail connector (for example Claude Cowork's
Google Workspace connector), it can do much of the inbox work without `gog`:
searching the inbox, reading threads, and archiving. You connect Gmail once in
the agent's own UI, with no Cloud Console setup.

Two steps still go through `gog`, because they need the real file:

- **Downloading the invoice PDF to disk.** A Gmail connector returns an
  attachment's name and id but not its bytes, and Fetch classifies, files, and
  attaches actual files on disk.
- **Attaching those PDFs to the draft reply.** `draft_reply` reliably attaches
  local files; building a draft with attachments straight from the connector is
  not confirmed yet.

So a clean split is: let the connector search, read, and archive; use Fetch
(which calls `gog`) for `download_attachment`, `classify_invoice(pdf_path=…)`,
and `draft_reply(attachments=[…])`. Both `classify_invoice` and `draft_reply`
take any local path, so however a PDF gets onto disk, Fetch can use it.

Inside Cowork, `gog` runs in the sandbox VM, so enable network egress and
allowlist `googleapis.com` and `accounts.google.com` so it can reach Google.
This last part is worth a quick test in your own Cowork session; we cannot
confirm the egress allowlist behaviour from outside it.

## Fetch invoices that are not in your inbox

Some invoices arrive through a vendor's billing portal or an e-invoice platform, not email. `plan_retrieval` turns each missing item into a task your agent can act on.

Teach it where to look per vendor by adding recipes to `config.yml`:

```yaml
vendor_sources:
  acme:                                  # matched against the vendor name
    portal_url: "https://billing.acme.com/invoices"
    login_hint: "log in with the company Google account, invoices under Billing"
    notes: "monthly subscription, issued on the 1st"
```

With a recipe, the task tells your agent the exact URL and how to log in. Without one, it suggests the usual places (the vendor portal, your e-invoice platform, or asking the vendor to resend). Your agent does the fetching with its own browser. The toolkit never holds portal logins. Add recipes over time and they become a reusable library for your company.

## Configuration

All settings live in `config.yml` (gitignored). Copy `config.example.yml` and edit:

```yaml
default_slug: my-company

companies:
  my-company: "My Company OÜ"

dropbox_dirs:                        # any folder, not only Dropbox
  my-company: "/path/to/My Company/Invoices"

debtor_accounts:
  my-company:
    name: "My Company OÜ"
    iban: "EE000000000000000000"
    bic: "LHVBEE22"

vendor_sources: {}   # optional, see "Fetch invoices that are not in your inbox"
email_accounts: []   # optional, see "Multiple email accounts"
```

Point at a config elsewhere with `FETCH_CONFIG`. Change the output folder with `FETCH_OUTPUT_DIR`.

## Commands

```
fetch onboarding                  Check prerequisites
fetch mcp                         Run the MCP server (stdio) for agents
fetch invoices fetch              Fetch and classify invoices from Gmail
fetch invoices to-dropbox         Copy classified invoices to each company's folder
fetch invoices generate-payments  Build SEPA payment XML for to-pay invoices
fetch invoices reconcile          Reconcile a missing-invoice list
fetch invoices archive            Review and archive Gmail threads
```

Use `-h` on any command for options.

## Development

```bash
uvx ruff check src/      # lint
uvx ruff format src/     # format
```

Style: single quotes, spaces, Python 3.11+. Config is in `pyproject.toml`.

## Project structure

```
fetch              Shell entry point (uv run python -m src)
config.example.yml  Config template (copy to config.yml)
src/
  __init__.py       Click root group (+ the mcp command)
  mcp_server.py     MCP server: all the agent tools
  onboarding.py     Setup check
  invoices/
    reconcile.py    Parse a missing list, match it, build a report
    retrieval.py    Plan fetching invoices not in the inbox
    audit.py        Local audit log
    providers.py    Mailbox abstraction + multi-account search
    gmail.py        Google-hosted mailboxes (via gog), draft replies
    imap.py         Any other mailbox via IMAP
    classify.py     AI classification (claude)
    payment_xml.py  SEPA payment XML
    config.py, models.py, helpers.py, process.py, dropbox.py, summary.py
```

## License

Apache-2.0. See [LICENSE](LICENSE).
