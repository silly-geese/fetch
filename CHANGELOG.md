# Changelog

## 0.1.0

First release. Fetch turns an accountant's "missing invoices" list into a draft reply with the files attached. It runs locally and your own agent drives it.

Added:
- MCP server with 16 tools, plus the `./fetch` CLI.
- Clear a missing-invoice list: parse the list, fetch invoices from Gmail, plan fetching the rest, and draft a reply to the accountant.
- Reconcile a missing list against what you found, and build a report.
- Fetch and classify invoices from Gmail, file them to a folder of your choice (Dropbox or any path), and build SEPA payment XML.
- Beyond-inbox retrieval with per-vendor recipes (`config.yml` `vendor_sources`). Your agent fetches with its own browser. No portal logins are stored.
- Local audit log of every fetch, download, and draft.

Safety:
- No stored credentials. Uses your local `gog` and `claude` CLIs.
- Replies are drafts you review before sending.
