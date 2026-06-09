"""MCP server exposing fetch's invoice toolkit to any MCP-capable agent.

This wraps the existing Gmail-fetch / classify / SEPA / Dropbox functions so a
host agent (Claude Code or any MCP client) can fetch, classify, file,
and pay invoices from the user's own inbox. The server holds no credentials —
it shells out to the user's local ``gog`` (Gmail) and ``claude`` CLIs.

Launch it over stdio with:

    uv run fetch-mcp            # installed console script (after `uv sync`)
    ./fetch mcp                 # via the repo's wrapper

All human-readable logging is routed to stderr so stdout stays a clean
JSON-RPC stream for the stdio transport.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP

from src.invoices import payment_xml
from src.invoices.audit import log_event, read_audit_log
from src.invoices.classify import classify_pdf
from src.invoices.config import (
    BASE_DIR,
    COMPANIES,
    DEBTOR_ACCOUNTS,
    GMAIL_QUERY,
    VENDOR_SOURCES,
)
from src.invoices.dropbox import copy_to_dropbox as _copy_to_dropbox
from src.invoices.gmail import (
    archive_thread as _archive_thread,
)
from src.invoices.gmail import (
    create_directories,
    search_messages,
)
from src.invoices.gmail import (
    create_draft_reply as _create_draft_reply,
)
from src.invoices.gmail import (
    download_attachment as _download_attachment,
)
from src.invoices.gmail import (
    fetch_message as _fetch_message,
)
from src.invoices.helpers import console, sha1_file
from src.invoices.models import InvoiceRecord, record_to_dict
from src.invoices.process import process_messages
from src.invoices.reconcile import (
    build_report as _build_report,
)
from src.invoices.reconcile import (
    item_from_dict,
)
from src.invoices.reconcile import (
    parse_missing_list as _parse_missing_list,
)
from src.invoices.reconcile import (
    reconcile as _reconcile,
)
from src.invoices.retrieval import plan_retrieval as _plan_retrieval
from src.invoices.summary import write_summary
from src.onboarding import check_prerequisites

# Route the shared rich console to stderr so its output never corrupts the
# stdio JSON-RPC stream on stdout. Every module imports this same console
# object, so reassigning its file here redirects logging everywhere.
console.file = sys.stderr

mcp = FastMCP('fetch-invoices')

_STATUS_ALIASES = {'paid': 'Paid', 'to-pay': 'To-Pay'}


def _load_records() -> list[InvoiceRecord]:
    """Load persisted invoice records from the last fetch (invoices.json)."""
    json_path = BASE_DIR / 'invoices.json'
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text())
    return [InvoiceRecord(**d) for d in data]


def _normalize_status(status: str | None) -> str | None:
    """Map a user-supplied status to the canonical 'Paid'/'To-Pay', or None.

    Raises ValueError for unrecognized values so an agent gets actionable
    feedback instead of a silently empty result (mirrors the CLI's validation).
    """
    if not status or status.lower() == 'all':
        return None
    norm = _STATUS_ALIASES.get(status.lower())
    if norm is None:
        raise ValueError(
            f'Unknown status {status!r}; expected one of: paid, to-pay, all'
        )
    return norm


def _validate_companies(companies: list[str] | None) -> None:
    """Raise ValueError if any company slug is unknown (mirrors the CLI)."""
    if not companies:
        return
    unknown = set(companies) - set(COMPANIES)
    if unknown:
        raise ValueError(
            f'Unknown company slugs: {", ".join(sorted(unknown))}. '
            f'Valid slugs: {", ".join(sorted(COMPANIES))}'
        )


@mcp.tool()
async def health_check() -> dict:
    """Verify prerequisites (gog CLI, claude CLI, Gmail auth, config.yml).

    Returns a structured report so an agent can self-diagnose setup problems
    before calling the other tools.
    """
    # check_prerequisites() shells out to `gog auth list` (blocking); run it off
    # the event loop so the stdio server stays responsive.
    return await anyio.to_thread.run_sync(check_prerequisites)


@mcp.tool()
async def search_inbox(
    query: str | None = None, max_results: int | None = None
) -> dict:
    """Search the user's Gmail and return matching message/thread ids.

    ``query`` is a Gmail search expression (e.g. "from:vendor has:attachment").
    When omitted, the built-in invoice query is used. Useful to preview what
    ``fetch_invoices`` would process, or to locate a specific email.
    """
    messages = await search_messages(max_results, query)
    return {
        'query': query or GMAIL_QUERY,
        'count': len(messages),
        'messages': messages,
    }


@mcp.tool()
async def fetch_invoices(
    max_emails: int | None = None, query: str | None = None
) -> dict:
    """Fetch invoices from Gmail, classify each PDF with AI, and file them.

    Runs the full pipeline: search Gmail -> download PDF attachments ->
    classify (company, status, amounts, beneficiary bank details) -> dedupe ->
    organize under the output directory -> write invoices.json + SUMMARY.md.

    ``query`` overrides the default invoice search; ``max_emails`` caps how many
    messages are processed. Returns the classified records and output paths.
    """
    create_directories()
    messages = await search_messages(max_emails, query)
    if not messages:
        log_event('fetch_invoices', {'count': 0, 'query': query or GMAIL_QUERY})
        # No fetch happened, so no summary/json was written; keep the documented
        # keys present (as None) so callers can index them unconditionally.
        return {
            'count': 0,
            'invoices': [],
            'output_dir': str(BASE_DIR),
            'summary_md': None,
            'invoices_json': None,
            'message': 'No matching messages found.',
        }

    records = await process_messages(messages)
    write_summary(records)
    log_event('fetch_invoices', {'count': len(records), 'query': query or GMAIL_QUERY})
    return {
        'count': len(records),
        'invoices': [record_to_dict(r) for r in records],
        'output_dir': str(BASE_DIR),
        'summary_md': str(BASE_DIR / 'SUMMARY.md'),
        'invoices_json': str(BASE_DIR / 'invoices.json'),
    }


@mcp.tool()
async def classify_invoice(
    pdf_path: str, subject: str = '', sender: str = '', snippet: str = ''
) -> dict:
    """Classify a single local PDF invoice/receipt with AI.

    Use this when you already have a PDF on disk (e.g. downloaded from a vendor
    portal) and want it classified into the same shape ``fetch_invoices``
    produces — document type, status, company, amounts, and beneficiary bank
    details. Does not move or file the PDF. ``subject``/``sender``/``snippet``
    add optional context for a better classification.
    """
    path = Path(pdf_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f'PDF not found: {path}')

    result = await classify_pdf(path, subject, sender, snippet, '')
    if result is None:
        raise RuntimeError(f'Classification failed for {path}')

    # Normalize into the same record shape fetch_invoices/list_invoices emit
    # (company/renamed_pdf, etc.) so all three tools return interchangeable dicts.
    record = InvoiceRecord(
        subject=subject,
        sender=sender,
        renamed_pdf=result.filename,
        amount_ex_vat=result.amount_ex_vat,
        amount_inc_vat=result.amount_inc_vat,
        summary=result.summary,
        status=result.status,
        company=result.slug,
        reason=result.reason,
        currency_code=result.currency_code,
        doc_type=result.doc_type,
        is_overdue=result.is_overdue,
        doc_date=result.doc_date,
        thread_id='',
        sha1=sha1_file(path),
        invoice_number=result.invoice_number,
        beneficiary_name=result.beneficiary_name,
        beneficiary_iban=result.beneficiary_iban,
        beneficiary_bic=result.beneficiary_bic,
    )
    return record_to_dict(record)


@mcp.tool()
def list_invoices(status: str | None = None, company: str | None = None) -> dict:
    """List invoices from the most recent fetch (reads invoices.json).

    Filter by ``status`` ("Paid" or "To-Pay", case-insensitive) and/or
    ``company`` slug. Returns the stored records without re-fetching from Gmail.
    """
    _validate_companies([company] if company else None)
    records = _load_records()
    norm = _normalize_status(status)
    if norm:
        records = [r for r in records if r.status == norm]
    if company:
        records = [r for r in records if r.company == company]
    return {
        'count': len(records),
        'invoices': [record_to_dict(r) for r in records],
    }


@mcp.tool()
async def copy_to_dropbox(
    status: str | None = None, companies: list[str] | None = None
) -> dict:
    """Copy classified invoices into their Dropbox company folders.

    Reads invoices.json, optionally filters by ``status`` ("Paid"/"To-Pay") and
    ``companies`` (slugs), then copies each PDF from the output tree into the
    Dropbox folder configured for its company. Returns per-file results.
    """
    _validate_companies(companies)
    records = _load_records()
    if not records:
        return {
            'copied': 0,
            'skipped': 0,
            'errors': 0,
            'details': [],
            'message': 'No invoices.json found — run fetch_invoices first.',
        }

    norm = _normalize_status(status)
    if norm:
        records = [r for r in records if r.status == norm]
    if companies:
        records = [r for r in records if r.company in companies]
    # shutil.copy2 over (possibly networked) Dropbox paths can block — offload
    # it so the stdio server's event loop stays responsive.
    return await anyio.to_thread.run_sync(_copy_to_dropbox, records)


@mcp.tool()
def generate_payments(
    companies: list[str] | None = None, execution_date: str | None = None
) -> dict:
    """Generate SEPA pain.001.001.03 payment XML for To-Pay invoices.

    Reads invoices.json, selects To-Pay invoices (optionally filtered by
    ``companies`` slugs), derives missing BICs from Estonian IBANs, and writes
    one XML file per debtor company under the output directory. Returns the
    written file paths plus any invoices skipped for missing bank details.

    ``execution_date`` is the requested payment date (YYYY-MM-DD); defaults to today.
    """
    _validate_companies(companies)
    records = [r for r in _load_records() if r.status == 'To-Pay']
    if companies:
        records = [r for r in records if r.company in companies]

    derived = payment_xml.fill_missing_bics(records)
    skipped = [
        {'pdf': r.renamed_pdf, 'missing': missing}
        for r, missing in payment_xml.incomplete_beneficiaries(records)
    ]
    paths = payment_xml.generate_payment_xml(
        records, DEBTOR_ACCOUNTS, BASE_DIR, execution_date
    )
    return {
        'count': len(paths),
        'files': [str(p) for p in paths],
        'derived_bics': [{'pdf': pdf, 'bic': bic} for pdf, bic in derived],
        'skipped': skipped,
    }


@mcp.tool()
async def archive_thread(thread_id: str) -> dict:
    """Archive a Gmail thread (remove its INBOX label) by thread id."""
    await _archive_thread(thread_id)
    return {'archived': thread_id}


@mcp.tool()
async def get_message(message_id: str) -> dict:
    """Fetch one Gmail message's headers and attachment list.

    Use after ``search_inbox`` to inspect a candidate email and pick which PDF to
    pull with ``download_attachment``. Returns subject/sender/date/snippet and
    ``attachments[{attachment_id, filename, mime_type}]``.
    """
    msg = await _fetch_message(message_id)
    headers = msg.get('headers', {})
    headers = headers if isinstance(headers, dict) else {}
    attachments = [
        {
            'attachment_id': a.get('attachmentId'),
            'filename': a.get('filename'),
            'mime_type': a.get('mimeType'),
        }
        for a in msg.get('attachments', [])
    ]
    return {
        'message_id': message_id,
        'thread_id': msg.get('threadId', ''),
        'subject': headers.get('subject', ''),
        'sender': headers.get('from', ''),
        'date': headers.get('date', ''),
        'snippet': msg.get('snippet', '') or (msg.get('body', '') or '')[:300],
        'attachments': attachments,
    }


@mcp.tool()
async def download_attachment(
    message_id: str, attachment_id: str, filename: str
) -> dict:
    """Download one Gmail attachment to the staging dir; returns its local path.

    Pair with ``get_message`` to retrieve the invoice PDF for a checklist item,
    then attach the path via ``draft_reply``.
    """
    path = await _download_attachment(message_id, attachment_id, filename)
    log_event(
        'download_attachment',
        {'message_id': message_id, 'filename': filename, 'path': str(path)},
    )
    return {'path': str(path), 'filename': filename, 'exists': path.exists()}


@mcp.tool()
async def parse_missing_list(text: str) -> dict:
    """Parse an accountant's free-form missing/expected-invoice list into a
    structured checklist.

    Accepts pasted text, CSV, a table, or an email body. Returns
    ``{count, items}`` where each item has ``vendor``, ``description``,
    ``amount``, ``currency``, ``period`` (YYYY-MM), ``invoice_number``, and the
    original ``raw`` text. Feed ``items`` into ``reconcile``.
    """
    items = await _parse_missing_list(text)
    return {'count': len(items), 'items': [asdict(i) for i in items]}


@mcp.tool()
def reconcile(checklist: list[dict], collected: list[dict] | None = None) -> dict:
    """Match a checklist (from ``parse_missing_list``) against collected invoices.

    ``checklist`` is the list of items to look for. ``collected`` is the invoices
    to match against (the records ``fetch_invoices``/``list_invoices`` return); if
    omitted, the most recent fetch (invoices.json) is used. Matching is
    deterministic — on invoice number, amount, period, and vendor name.

    Returns ``{summary, matched, still_missing, unmatched_collected}``;
    ``still_missing`` is what to go retrieve next (inbox / e-invoice / portal).
    """
    items = [item_from_dict(c) for c in checklist]
    records = (
        collected
        if collected is not None
        else [record_to_dict(r) for r in _load_records()]
    )
    return _reconcile(items, records)


@mcp.tool()
def build_report(reconciliation: dict, title: str | None = None) -> dict:
    """Render a ``reconcile`` result as a shareable Markdown report.

    Writes ``REPORT.md`` under the output directory and returns
    ``{report_md, path}``. Give this to the accountant to show what was cleared
    and what is still outstanding.
    """
    md = _build_report(reconciliation, title or 'Missing-Invoice Reconciliation')
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = BASE_DIR / 'REPORT.md'
    path.write_text(md)
    return {'report_md': md, 'path': str(path)}


@mcp.tool()
def plan_retrieval(items: list[dict]) -> dict:
    """Plan how to fetch invoices that were NOT found in the inbox.

    Give it the still-missing checklist items (e.g. ``reconcile`` `still_missing`).
    For each, returns a retrieval task: the vendor/identifiers, any per-vendor
    recipe configured in ``config.yml`` (`vendor_sources`), suggested sources
    (vendor portal, e-invoice platform), and step-by-step instructions. The host
    agent then uses its OWN browser/web tools to fetch each PDF and attaches the
    files via ``draft_reply``. (No credentials are held by this server.)
    """
    tasks = _plan_retrieval(items, VENDOR_SOURCES)
    return {'count': len(tasks), 'tasks': tasks}


@mcp.tool()
async def draft_reply(
    body: str,
    attachments: list[str] | None = None,
    reply_to_message_id: str | None = None,
    to: str | None = None,
    subject: str | None = None,
) -> dict:
    """Create a DRAFT Gmail reply to the accountant with the invoice files attached.

    This is the deliverable: a draft (never auto-sent) the human reviews and sends.
    Provide ``reply_to_message_id`` (the Gmail *message id* of the accountant's
    request email, e.g. from ``search_inbox``/``get_message``) or ``to`` (an email
    address). ``attachments`` is a list of local PDF paths gathered from the inbox
    and/or beyond-inbox retrieval. Put the "still couldn't find: …" note in ``body``.
    """
    result = await _create_draft_reply(
        body=body,
        attachments=attachments or [],
        reply_to_message_id=reply_to_message_id,
        to=to,
        subject=subject,
    )
    log_event(
        'draft_reply',
        {
            'reply_to_message_id': reply_to_message_id,
            'to': to,
            'attachment_count': len(attachments or []),
            'attachments': attachments or [],
        },
    )
    return result


@mcp.tool()
def read_audit(limit: int | None = None) -> dict:
    """Read the local audit log of retrieval/delivery actions.

    Returns ``{count, entries}`` (most recent last); ``limit`` keeps the last N.
    Every fetch, download, and draft is recorded in ``<output dir>/audit.log``.
    """
    entries = read_audit_log(limit)
    return {'count': len(entries), 'entries': entries}


def main() -> None:
    """Run the MCP server over stdio. Entry point for ``fetch-mcp`` / ``./fetch mcp``."""
    mcp.run()


if __name__ == '__main__':
    main()
