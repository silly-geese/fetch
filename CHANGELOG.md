# Changelog

## Unreleased

Added:
- Multiple email accounts. List mailboxes under `email_accounts` in config.yml: Google-hosted addresses on any domain (via gog) and any other mailbox via IMAP. One run searches them all, and one failing account no longer stops the rest. IMAP passwords come from environment variables, never from config.
- Per-account setup checks. Onboarding and health_check verify gog auth for each Google account, warn when a gmail-configured domain is not actually Google-hosted, note the "External" OAuth client requirement for multi-domain setups, and login-test IMAP accounts.

Changed:
- Message, thread, and attachment ids are account-scoped. The mailbox MCP tools (search_inbox, get_message, download_attachment, archive_thread, draft_reply) take an optional `account` parameter; it is only needed when several accounts are configured.
- Staged attachment downloads are namespaced by account as well as message, so same-named PDFs from different mailboxes cannot overwrite each other.

## 0.1.1

Fixes and docs. No breaking changes.

Fixed:
- The MCP server now starts even when config.yml is missing or invalid. health_check reports the problem instead of the client showing a blank disconnect, and operations that need config fail with a clear message.
- Corrected the gog install hint (the formula moved to the openclaw tap).

Changed:
- Destination folders (dropbox_dirs) can be any path, not only Dropbox.

Added:
- Security policy (SECURITY.md) and hardening against argument injection in Gmail ids.
- Guidance for connecting Gmail without a Google Cloud project (GOG_ACCESS_TOKEN) and for using a host agent's own Gmail connector, such as Cowork.

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
