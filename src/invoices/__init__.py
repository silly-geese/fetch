import asyncio
import json
import shutil
import sys
import termios
from pathlib import Path

import click
from rich.panel import Panel
from rich.prompt import Confirm

from src import fetch

from .config import BASE_DIR, COMPANIES, DEBTOR_ACCOUNTS, require_config
from .dropbox import copy_to_dropbox
from .gmail import archive_thread, create_directories, search_messages
from .helpers import console
from .models import InvoiceRecord
from .payment_xml import (
    fill_missing_bics,
    generate_payment_xml,
    incomplete_beneficiaries,
)
from .process import process_messages
from .reconcile import build_report, parse_missing_list
from .reconcile import reconcile as reconcile_items
from .summary import write_summary


@fetch.group(context_settings={'help_option_names': ['-h', '--help']})
def invoices():
    """Download and classify Gmail invoice attachments."""


@invoices.command()
@click.option(
    '--max-emails', default=None, type=int, help='Limit number of emails to process'
)
def fetch(max_emails: int | None):
    """Fetch invoices from Gmail, classify, and organize."""
    # Save terminal state before asyncio takes over signal handling
    saved_attrs = None
    if sys.stdin.isatty():
        saved_attrs = termios.tcgetattr(sys.stdin)
    try:
        asyncio.run(_main(max_emails))
    except KeyboardInterrupt:
        console.print('\n[bold yellow]Interrupted.[/bold yellow]')
    finally:
        if saved_attrs is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved_attrs)


async def _main(max_emails: int | None):
    require_config()
    create_directories()
    message_dicts = await search_messages(max_emails)

    if not message_dicts:
        console.print('\n[bold]No messages found. Done.[/bold]')
        return

    records = await process_messages(message_dicts)
    write_summary(records)
    console.print(
        f'\n[bold green]Done![/bold green] Processed {len(records)} invoices.'
    )


_company_slugs = ', '.join(COMPANIES.keys())


@invoices.command('to-dropbox')
@click.option('--paid', is_flag=True, default=False, help='Only copy Paid invoices')
@click.option('--to-pay', is_flag=True, default=False, help='Only copy To-Pay invoices')
@click.option(
    '--companies',
    '-c',
    multiple=True,
    help=f'Filter by company slug (repeatable). Choices: {_company_slugs}',
)
def to_dropbox(paid: bool, to_pay: bool, companies: tuple[str, ...]):
    """Copy classified invoices from output/ to each company's folder (any path)."""
    require_config()
    json_path = BASE_DIR / 'invoices.json'
    if not json_path.exists():
        console.print(
            f"[red]Error:[/red] {json_path} not found. Run 'invoices fetch' first."
        )
        raise SystemExit(1)

    data = json.loads(json_path.read_text())
    records = [InvoiceRecord(**d) for d in data]

    if paid and not to_pay:
        records = [r for r in records if r.status == 'Paid']
    elif to_pay and not paid:
        records = [r for r in records if r.status == 'To-Pay']

    if companies:
        unknown = set(companies) - set(COMPANIES.keys())
        if unknown:
            console.print(
                f'[red]Error:[/red] Unknown company slugs: {", ".join(sorted(unknown))}'
            )
            console.print(f'Valid slugs: {_company_slugs}')
            raise SystemExit(1)
        records = [r for r in records if r.company in companies]

    if not records:
        console.print('[yellow]No invoices match the given filters.[/yellow]')
        return

    console.print(f'Copying {len(records)} invoice(s) to their folders…')
    copy_to_dropbox(records)


@invoices.command('generate-payments')
@click.option(
    '--companies',
    '-c',
    multiple=True,
    help=f'Filter by company slug (repeatable). Choices: {_company_slugs}',
)
@click.option(
    '--execution-date',
    '-d',
    default=None,
    help='Payment execution date (YYYY-MM-DD). Defaults to today.',
)
def generate_payments(companies: tuple[str, ...], execution_date: str | None):
    """Generate LHV pain.001.001.03 payment XML for To-Pay invoices."""
    require_config()
    json_path = BASE_DIR / 'invoices.json'
    if not json_path.exists():
        console.print(
            f"[red]Error:[/red] {json_path} not found. Run 'invoices fetch' first."
        )
        raise SystemExit(1)

    data = json.loads(json_path.read_text())
    records = [InvoiceRecord(**d) for d in data]

    # Only To-Pay invoices
    records = [r for r in records if r.status == 'To-Pay']

    if companies:
        unknown = set(companies) - set(COMPANIES.keys())
        if unknown:
            console.print(
                f'[red]Error:[/red] Unknown company slugs: {", ".join(sorted(unknown))}'
            )
            console.print(f'Valid slugs: {_company_slugs}')
            raise SystemExit(1)
        records = [r for r in records if r.company in companies]

    if not records:
        console.print('[yellow]No To-Pay invoices match the given filters.[/yellow]')
        return

    # Attempt to fill in missing BICs from Estonian IBANs
    for pdf_name, derived_bic in fill_missing_bics(records):
        console.print(
            f'  [green]Derived BIC {derived_bic} from IBAN for {pdf_name}[/green]'
        )

    # Warn about invoices that will be skipped due to missing beneficiary details
    skipped = incomplete_beneficiaries(records)
    if skipped:
        console.print(
            f'\n[yellow]Warning: {len(skipped)} invoice(s) will be skipped (missing beneficiary details):[/yellow]'
        )
        for r, missing in skipped:
            console.print(
                f'  [yellow]•[/yellow] {r.renamed_pdf}: missing {", ".join(missing)}'
            )
        console.print()

    paths = generate_payment_xml(records, DEBTOR_ACCOUNTS, BASE_DIR, execution_date)

    if not paths:
        console.print(
            '[yellow]No payment XML files generated (missing beneficiary details or debtor accounts).[/yellow]'
        )
        return

    console.print(
        f'[bold green]Generated {len(paths)} payment XML file(s):[/bold green]'
    )
    for p in paths:
        console.print(f'  {p}')


@invoices.command()
@click.option(
    '--status',
    type=click.Choice(['paid', 'to-pay', 'all'], case_sensitive=False),
    default='all',
    help='Filter by invoice status.',
)
@click.option(
    '--companies',
    '-c',
    multiple=True,
    help=f'Filter by company slug (repeatable). Choices: {_company_slugs}',
)
@click.option(
    '--max-emails', default=None, type=int, help='Limit number of threads to process'
)
@click.option(
    '--dry-run', '-n', is_flag=True, default=False, help='Preview without archiving'
)
@click.option(
    '--yes', '-y', is_flag=True, default=False, help='Skip prompts, archive all matches'
)
def archive(
    status: str,
    companies: tuple[str, ...],
    max_emails: int | None,
    dry_run: bool,
    yes: bool,
):
    """Interactively review and archive invoice threads from Gmail."""
    json_path = BASE_DIR / 'invoices.json'
    if not json_path.exists():
        console.print(
            f"[red]Error:[/red] {json_path} not found. Run 'invoices fetch' first."
        )
        raise SystemExit(1)

    data = json.loads(json_path.read_text())
    records = [InvoiceRecord(**d) for d in data]

    # Filter by status
    status_map = {'paid': 'Paid', 'to-pay': 'To-Pay'}
    if status != 'all':
        target = status_map[status]
        records = [r for r in records if r.status == target]

    # Filter by company
    if companies:
        unknown = set(companies) - set(COMPANIES.keys())
        if unknown:
            console.print(
                f'[red]Error:[/red] Unknown company slugs: {", ".join(sorted(unknown))}'
            )
            console.print(f'Valid slugs: {_company_slugs}')
            raise SystemExit(1)
        records = [r for r in records if r.company in companies]

    if not records:
        console.print('[yellow]No invoices match the given filters.[/yellow]')
        return

    # Deduplicate by thread_id — group records per thread
    threads: dict[str, list[InvoiceRecord]] = {}
    for r in records:
        if not r.thread_id:
            continue
        threads.setdefault(r.thread_id, []).append(r)

    if not threads:
        console.print('[yellow]No threads found (records missing thread_id).[/yellow]')
        return

    thread_items = list(threads.items())
    if max_emails is not None:
        thread_items = thread_items[:max_emails]

    console.print(f'\nFound [bold]{len(thread_items)}[/bold] thread(s) to review.\n')

    archived = 0
    kept = 0

    saved_attrs = None
    if sys.stdin.isatty():
        saved_attrs = termios.tcgetattr(sys.stdin)

    try:
        for thread_id, thread_records in thread_items:
            first = thread_records[0]
            pdf_names = ', '.join(r.renamed_pdf for r in thread_records)
            amount_str = (
                f'{first.currency_code} {first.amount_inc_vat}'
                if first.amount_inc_vat
                else 'N/A'
            )
            gmail_link = f'https://mail.google.com/mail/u/0/#inbox/{thread_id}'

            date_str = first.doc_date or 'N/A'

            panel_text = (
                f'[bold]Subject:[/bold] {first.subject}\n'
                f'[bold]From:[/bold] {first.sender}\n'
                f'[bold]Date:[/bold] {date_str}\n'
                f'[bold]Status:[/bold] {first.status}\n'
                f'[bold]Company:[/bold] {first.company}\n'
                f'[bold]Amount:[/bold] {amount_str}\n'
                f'[bold]Summary:[/bold] {first.summary}\n'
                f'[bold]PDF(s):[/bold] {pdf_names}\n'
                f'[bold]Thread:[/bold] {gmail_link}'
            )
            console.print(Panel(panel_text, title=f'Thread {thread_id[:12]}…'))

            if dry_run:
                console.print('[dim]  (dry run — skipping)[/dim]\n')
                archived += 1
                continue

            should_archive = yes or Confirm.ask('  Archive this thread?', default=False)

            if should_archive:
                asyncio.run(archive_thread(thread_id))
                console.print('[green]  Archived.[/green]\n')
                archived += 1
            else:
                console.print('[dim]  Kept.[/dim]\n')
                kept += 1
    except KeyboardInterrupt:
        console.print('\n[bold yellow]Interrupted.[/bold yellow]')
    finally:
        if saved_attrs is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved_attrs)

    label = 'Would archive' if dry_run else 'Archived'
    console.print(f'\n[bold]{label}: {archived}[/bold], Kept: {kept}')


@invoices.command()
@click.option(
    '--yes', '-y', is_flag=True, default=False, help='Skip confirmation prompt'
)
def clean(yes: bool):
    """Delete the output folder and all its contents."""
    if not BASE_DIR.exists():
        console.print(f'[yellow]Nothing to clean — {BASE_DIR} does not exist.[/yellow]')
        return

    file_count = sum(1 for _ in BASE_DIR.rglob('*') if _.is_file())
    console.print(f'This will delete [bold]{BASE_DIR}[/bold] ({file_count} file(s)).')

    if not yes and not Confirm.ask('Proceed?', default=False):
        console.print('[dim]Cancelled.[/dim]')
        return

    shutil.rmtree(BASE_DIR)
    create_directories()
    console.print('[bold green]Done![/bold green] Output folder cleaned.')


@invoices.command('reconcile')
@click.option(
    '--file',
    '-f',
    'list_file',
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help='Path to the missing-invoice list (text, CSV, or email body).',
)
@click.option('--title', default=None, help='Report title.')
def reconcile_cmd(list_file: str, title: str | None):
    """Reconcile a missing-invoice list against fetched invoices."""
    json_path = BASE_DIR / 'invoices.json'
    if not json_path.exists():
        console.print(
            f"[red]Error:[/red] {json_path} not found. Run 'invoices fetch' first."
        )
        raise SystemExit(1)

    records = json.loads(json_path.read_text())

    console.print('Parsing missing-invoice list…')
    items = asyncio.run(parse_missing_list(Path(list_file).read_text()))
    if not items:
        console.print('[yellow]No checklist items parsed from the input.[/yellow]')
        return

    result = reconcile_items(items, records)
    report_path = BASE_DIR / 'REPORT.md'
    report_path.write_text(
        build_report(result, title or 'Missing-Invoice Reconciliation')
    )

    s = result['summary']
    console.print(
        f'\n[bold]Reconciliation:[/bold] {s["matched"]} matched, '
        f'[yellow]{s["still_missing"]} still missing[/yellow], '
        f'{s["unmatched_collected"]} collected but not listed'
    )
    console.print(f'Report written to [bold]{report_path}[/bold]')
