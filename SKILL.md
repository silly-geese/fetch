# Fetch Invoices: agent skill

An MCP server that lets **your own AI agent** fetch, classify, file, and pay
invoices straight from your inbox. You bring the agent and the credentials; this
toolkit brings the domain logic. Estonia-first (SEPA / LHV / Estonian IBANs).

## Credential model: nothing is held here

This server stores **no passwords or tokens**. It runs locally and shells out to
*your* already-authenticated CLIs:

- **`gog`**: your Gmail session (search, fetch attachments, draft replies).
- **`claude`**: classifies each PDF (runs the `haiku` model locally).

If those aren't set up, every tool can tell you: call `health_check` first.

## Launching the server

The server speaks MCP over **stdio**. Register it with your MCP client. The
simplest way needs no clone: `uvx` runs it straight from the repo, and you point
`FETCH_CONFIG` at your config file and `FETCH_OUTPUT_DIR` at an output folder.

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

If you cloned the repo instead, point at the clone:
`command "uv"`, `args ["run", "--directory", "/path/to/clone", "fetch-mcp"]`
(or just `./fetch mcp`). With a clone, `config.yml` is found next to the source
automatically.

One-time setup the human does: create `config.yml` from `config.example.yml`
(companies, the folder to file each company's invoices into — any folder, not
only Dropbox — and debtor bank accounts). Verify with
`./fetch onboarding` or the `health_check` tool.

> Output (the `output/` tree, `invoices.json`, `SUMMARY.md`, `REPORT.md`,
> `audit.log`, payment XML) is written under the working directory, or under
> `$FETCH_OUTPUT_DIR` if set.

## Tools

| Tool | Purpose |
|------|---------|
| `health_check()` | Check gog CLI, claude CLI, Gmail auth, and config.yml. Returns `{ok, checks[]}`. Call this first. |
| `search_inbox(query?, max_results?)` | Raw Gmail search, returns `{query, count, messages[{id, threadId}]}`. `query` defaults to the built-in invoice search. |
| `get_message(message_id)` | One message's headers plus `attachments[{attachment_id, filename, mime_type}]`. Use it to pick which PDF to pull. |
| `download_attachment(message_id, attachment_id, filename)` | Download one attachment to staging, returns `{path, filename, exists}`. |
| `fetch_invoices(max_emails?, query?)` | The full pipeline: search Gmail, download PDFs, classify, dedupe, file under `output/`, write `invoices.json` and `SUMMARY.md`. Returns `{count, invoices[], output_dir, summary_md, invoices_json}`. |
| `classify_invoice(pdf_path, subject?, sender?, snippet?)` | Classify one local PDF (e.g. one you downloaded yourself) into the same record shape. Does **not** move the file. |
| `list_invoices(status?, company?)` | Read back the last fetch from `invoices.json`, filtered by status (`Paid`/`To-Pay`) and/or company slug. No network. |
| `copy_to_dropbox(status?, companies?)` | Copy filed PDFs into each company's configured folder (`dropbox_dirs` in config.yml — any folder, not only Dropbox). Returns `{copied, skipped, errors, details[]}`. |
| `generate_payments(companies?, execution_date?)` | Build SEPA `pain.001.001.03` XML for To-Pay invoices, one file per debtor company. Derives missing BICs from Estonian IBANs. Returns `{count, files[], derived_bics[], skipped[]}`. |
| `archive_thread(thread_id)` | Remove the INBOX label from a Gmail thread. |
| `parse_missing_list(text)` | Turn an accountant's free-form missing/expected-invoice list (pasted text, CSV, table, email) into a structured checklist, returns `{count, items[]}`. |
| `reconcile(checklist, collected?)` | Match the checklist against collected invoices (or the last fetch if `collected` omitted). Returns `{summary, matched[], still_missing[], unmatched_collected[]}`. Deterministic. |
| `build_report(reconciliation, title?)` | Render a reconcile result as Markdown, write `REPORT.md`, return `{report_md, path}`. Optional summary for the accountant. |
| `plan_retrieval(items)` | For invoices NOT in the inbox, return retrieval tasks per item (vendor, identifiers, any `config.yml` per-vendor recipe, suggested sources, instructions). You fetch each with your own browser, then attach via `draft_reply`. |
| `draft_reply(body, attachments?, reply_to_message_id?, to?, subject?)` | Create a **draft** Gmail reply to the accountant with the invoice files attached. Never auto-sends; a human reviews and sends. |
| `read_audit(limit?)` | Read the local audit log (`output/audit.log`). Every fetch, download, and draft is recorded. |

Statuses are case-insensitive (`paid`, `to-pay`, `all`). Company values are the
slugs from `config.yml`.

## Main workflow: clear a missing-invoice list

The core job: the accountant emails a list of invoices they're missing, you fetch
the files, you reply with them attached.

1. **`parse_missing_list(text=…)`**: paste the accountant's list (any format) to
   get a checklist.
2. **Fetch from the inbox**, per checklist item:
   - **`search_inbox(query=…)`** with a targeted query (vendor, invoice number, month).
   - **`get_message(message_id)`** on the best hit to see its attachments.
   - **`download_attachment(...)`** to pull the invoice PDF to a local path.
3. **For anything still not in the inbox, call `plan_retrieval(items=…)`.** For
   each task, use your OWN browser/web tools to fetch the invoice (per the
   per-vendor recipe or suggested sources) and save the PDF locally.
4. **`draft_reply(to=<accountant's address>, attachments=[…paths], body=…)`**:
   creates a **draft** reply with every found PDF attached. Put "still couldn't
   find: …" in the body. A human reviews and sends. To thread it onto their
   original email, pass `reply_to_message_id=<that email's Gmail message id>`,
   which you can get via `search_inbox`/`get_message`. It is a message id, not an
   address.

Optional: `reconcile` then `build_report` if you want a structured
found/still-missing summary to paste into the reply body.

> **Beyond-inbox is host-agent delegation.** `plan_retrieval` tells you *what* to
> get and *where* to look; you (the agent) do the browser fetching. The server
> holds no portal credentials. Grow the recipe library via `vendor_sources` in
> `config.yml`.

## Other workflow: fetch, file, and pay from your inbox

1. **`health_check()`**: confirm gog, claude, Gmail auth, and config are ready.
2. **`fetch_invoices(max_emails=…)`**: pull and classify recent invoices. Start
   small, then run wider.
3. **`list_invoices(status="to-pay")`**: review what's outstanding.
4. **`copy_to_dropbox()`**: file the PDFs into each company's configured folder
   (any folder, not only Dropbox).
5. **`generate_payments()`**: produce the bank-ready SEPA XML for To-Pay
   invoices. Check `skipped[]` for anything missing bank details.
6. **`archive_thread(thread_id)`**: tidy the inbox once a thread is handled.

For an ad-hoc PDF you got outside Gmail, use **`classify_invoice(pdf_path=…)`**
to fold it into the same record shape.

## Roadmap (later)

Fully autonomous portal automation (driving the browser plus per-vendor recipes
inside the toolkit), and structured e-invoice operator pulls (Envoice / RIK /
Peppol) as additional fetch sources behind the same loop.
