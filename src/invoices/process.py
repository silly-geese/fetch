from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)

from .classify import _format_thread_context, classify_pdf
from .config import BASE_DIR, COMPANIES, STAGING_DIR, STATUSES
from .helpers import console, sha1_file
from .models import InvoiceRecord

if TYPE_CHECKING:
    from pathlib import Path

    from .models import Classification
    from .providers import MailProvider


def create_directories():
    console.rule('[bold]Step 1: Creating directory structure')
    for status in STATUSES:
        for slug in COMPANIES:
            d = BASE_DIR / status / slug
            d.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    console.print(
        f'  Created directories under [bold]{BASE_DIR}[/bold] and [bold]{STAGING_DIR}[/bold]'
    )


# ── Dedup state ──────────────────────────────────────────────────────────────

_dedup_lock = asyncio.Lock()
_seen_sha1s: dict[str, str] = {}  # sha1 → renamed filename


def is_duplicate(pdf_path, target_dir):
    """Check if a file with the same SHA1 already exists in target_dir."""
    checksum = sha1_file(pdf_path)
    for existing in target_dir.iterdir():
        if existing.is_file() and sha1_file(existing) == checksum:
            console.print(
                f'  [dim]SKIP duplicate: {pdf_path.name} matches {existing.name}[/dim]'
            )
            return True
    return False


# ── Processing ───────────────────────────────────────────────────────────────


async def process_thread(
    provider: MailProvider,
    thread_id: str,
    matched_message_ids: list[str],
    index: int,
    total: int,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    task_id,
) -> list[InvoiceRecord]:
    """Process an entire thread: fetch once, extract PDF attachments from all messages."""
    records: list[InvoiceRecord] = []

    async with semaphore:
        console.print(
            f'\n[bold]--- Thread {index}/{total}: {thread_id} [{provider.label}] '
            f'({len(matched_message_ids)} matched message(s)) ---[/bold]'
        )

        try:
            thread = await provider.fetch_thread(thread_id)
        except Exception as exc:
            console.print(f'  [red]ERROR:[/red] Could not fetch thread: {exc}')
            progress.advance(task_id)
            return records

        messages = thread.get('messages', [])
        if not messages:
            console.print('  [dim]No messages in thread[/dim]')
            progress.advance(task_id)
            return records

        # Classify all PDF attachments across all messages, then filter
        classified: list[tuple[Path, Classification]] = []

        for thread_msg in messages:
            mid = thread_msg.get('id', '')
            thread_context = _format_thread_context(thread, mid)

            # Thread messages use raw Gmail format; fetch normalized version
            try:
                msg = await provider.fetch_message(mid)
            except Exception as exc:
                console.print(f'  [dim]Could not fetch message {mid}: {exc}[/dim]')
                continue

            subject = msg.get('headers', {}).get('subject', '')
            sender = msg.get('headers', {}).get('from', '')
            snippet = msg.get('body', '')

            pdf_attachments = [
                a
                for a in msg.get('attachments', [])
                if a['filename'].lower().endswith('.pdf')
            ]

            if not pdf_attachments:
                continue

            console.print(f'\n  [bold]Message:[/bold] {mid}')
            console.print(f'  Subject: {subject}')
            console.print(f'  From: {sender}')

            for att in pdf_attachments:
                att_id = att['attachmentId']
                att_filename = att['filename']
                console.print(f'\n  Attachment: [cyan]{att_filename}[/cyan]')

                pdf_path = await provider.download_attachment(mid, att_id, att_filename)
                if not pdf_path.exists():
                    console.print(
                        f'  [red]ERROR:[/red] Download failed for {att_filename}'
                    )
                    continue

                classification = await classify_pdf(
                    pdf_path, subject, sender, snippet, thread_context
                )
                if classification is None:
                    console.print('  [red]ERROR:[/red] Classification failed, skipping')
                    continue

                overdue_tag = (
                    ' [red bold]OVERDUE[/red bold]' if classification.is_overdue else ''
                )
                console.print(
                    f'  Classification: type={classification.doc_type}, '
                    f'status={classification.status}, company={classification.slug}, '
                    f'amount_ex_vat={classification.amount_ex_vat}, '
                    f'amount_inc_vat={classification.amount_inc_vat}'
                    f'{overdue_tag}'
                )
                console.print(f'  Renamed to: [green]{classification.filename}[/green]')

                if classification.doc_type in ('other', 'reminder'):
                    label = (
                        'NOT an invoice or receipt'
                        if classification.doc_type == 'other'
                        else 'Payment reminder (not the actual invoice)'
                    )
                    console.print(f'  [dim]{label} — removing from staging[/dim]')
                    pdf_path.unlink(missing_ok=True)
                    continue

                classified.append((pdf_path, classification))

        # Correct misclassified invoices: if original filename says "invoice"
        # but classifier said "receipt" (e.g. email subject was "Your receipt..."),
        # trust the original filename.
        for pdf_path, classification in classified:
            if (
                classification.doc_type == 'receipt'
                and 'invoice' in pdf_path.stem.lower()
            ):
                classification.doc_type = 'invoice'
                classification.filename = classification.filename.replace(
                    'Receipt ', 'Invoice ', 1
                )
                console.print(
                    f'  [yellow]Corrected doc_type to invoice '
                    f'(original filename: {pdf_path.name})[/yellow]'
                )

        # If we have both an invoice and a receipt in the thread, drop receipts
        has_invoice = any(c.doc_type == 'invoice' for _, c in classified)
        if has_invoice:
            kept: list[tuple[Path, Classification]] = []
            for pdf_path, classification in classified:
                if classification.doc_type == 'receipt':
                    console.print(
                        f'  [dim]SKIP receipt (invoice exists in thread): {pdf_path.name}[/dim]'
                    )
                    pdf_path.unlink(missing_ok=True)
                else:
                    kept.append((pdf_path, classification))
            classified = kept

        for pdf_path, classification in classified:
            sha1 = sha1_file(pdf_path)

            async with _dedup_lock:
                if sha1 in _seen_sha1s:
                    console.print(
                        f'  [dim]SKIP global duplicate: {pdf_path.name} matches {_seen_sha1s[sha1]}[/dim]'
                    )
                    pdf_path.unlink(missing_ok=True)
                    continue

                target_dir = BASE_DIR / classification.status / classification.slug
                if is_duplicate(pdf_path, target_dir):
                    pdf_path.unlink(missing_ok=True)
                    continue

                _seen_sha1s[sha1] = classification.filename
                dest = target_dir / classification.filename
                shutil.move(str(pdf_path), str(dest))
                console.print(f'  Moved to: [green]{dest}[/green]')

            records.append(
                InvoiceRecord(
                    subject=subject,
                    sender=sender,
                    renamed_pdf=classification.filename,
                    amount_ex_vat=classification.amount_ex_vat,
                    amount_inc_vat=classification.amount_inc_vat,
                    summary=classification.summary,
                    status=classification.status,
                    company=classification.slug,
                    reason=classification.reason,
                    currency_code=classification.currency_code,
                    doc_type=classification.doc_type,
                    is_overdue=classification.is_overdue,
                    doc_date=classification.doc_date,
                    thread_id=thread_id,
                    account=provider.address,
                    sha1=sha1,
                    invoice_number=classification.invoice_number,
                    beneficiary_name=classification.beneficiary_name,
                    beneficiary_iban=classification.beneficiary_iban,
                    beneficiary_bic=classification.beneficiary_bic,
                )
            )

        progress.advance(task_id)

    return records


async def process_messages(message_dicts: list[dict]) -> list[InvoiceRecord]:
    console.rule('[bold]Step 3: Processing threads')

    # Group messages by (account, threadId) — thread IDs are account-scoped,
    # so every operation must go through the originating provider
    threads: dict[tuple[str, str], tuple[MailProvider, list[str]]] = {}
    for md in message_dicts:
        tid = md.get('threadId', '') or md['id']
        key = (md['account'], tid)
        threads.setdefault(key, (md['provider'], []))[1].append(md['id'])

    semaphore = asyncio.Semaphore(5)
    records: list[InvoiceRecord] = []

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task('Processing threads', total=len(threads))

        tasks = [
            asyncio.ensure_future(
                process_thread(
                    provider, tid, mids, i, len(threads), semaphore, progress, task_id
                )
            )
            for i, ((_account, tid), (provider, mids)) in enumerate(threads.items(), 1)
        ]

        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                records.extend(result)
            except Exception as exc:
                console.print(f'  [red]ERROR:[/red] Task failed: {exc}')

    return records
