import json

from rich.table import Table

from .config import BASE_DIR, COMPANIES
from .helpers import console, convert_to_eur
from .models import InvoiceRecord, record_to_dict


def _fmt_amount(amount: float | None) -> str:
    """Format a float amount for display, or 'N/A' if None."""
    if amount is None:
        return 'N/A'
    return f'€{amount:,.2f}'


def _md_cell(value: str) -> str:
    """Escape an untrusted string for a markdown table cell (subject/sender/etc.)."""
    return str(value or '').replace('|', r'\|').replace('\n', ' ').replace('\r', ' ')


def _md_invoice_table(
    section_records: list[InvoiceRecord], start_index: int = 1
) -> list[str]:
    """Generate markdown table rows for a list of invoice records."""
    lines = []
    lines.append(
        '| # | Type | Subject | From | PDF | Amount (ex. VAT) | Amount (inc. VAT) | EUR ex. VAT | EUR inc. VAT | Currency | Description | Company | Overdue | Reason |'
    )
    lines.append(
        '|---|------|---------|------|-----|------------------|-------------------|-------------|--------------|----------|-------------|---------|---------|--------|'
    )
    for i, r in enumerate(section_records, start_index):
        subject = _md_cell(r.subject)
        if r.thread_id:
            subject_cell = (
                f'[{subject}](https://mail.google.com/mail/u/0/#inbox/{r.thread_id})'
            )
        else:
            subject_cell = subject
        pretty_company = COMPANIES.get(r.company, r.company)
        eur_ex = convert_to_eur(r.amount_ex_vat, r.currency_code)
        eur_inc = convert_to_eur(r.amount_inc_vat, r.currency_code)
        overdue_cell = '⚠️ OVERDUE' if r.is_overdue else ''
        type_cell = r.doc_type.capitalize()
        lines.append(
            f'| {i} | {type_cell} | {subject_cell} | {_md_cell(r.sender)} | {_md_cell(r.renamed_pdf)} | '
            f'{_fmt_amount(r.amount_ex_vat)} | {_fmt_amount(r.amount_inc_vat)} | {_fmt_amount(eur_ex)} | {_fmt_amount(eur_inc)} | {r.currency_code} | {_md_cell(r.summary)} | {pretty_company} | {overdue_cell} | {_md_cell(r.reason)} |'
        )
    return lines


def _md_totals_table(section_records: list[InvoiceRecord]) -> list[str]:
    """Generate a per-company totals table with EUR sums."""
    lines = []
    lines.append(
        '| Company | # Invoices | EUR Total ex. VAT | EUR Total inc. VAT | Notes |'
    )
    lines.append(
        '|---------|------------|--------------------|--------------------|-------|'
    )
    company_stats: dict[str, dict] = {}
    for r in section_records:
        if r.company not in company_stats:
            company_stats[r.company] = {
                'count': 0,
                'eur_ex': 0.0,
                'eur_inc': 0.0,
                'na_count': 0,
            }
        company_stats[r.company]['count'] += 1
        eur_ex = convert_to_eur(r.amount_ex_vat, r.currency_code)
        eur_inc = convert_to_eur(r.amount_inc_vat, r.currency_code)
        if eur_ex is not None:
            company_stats[r.company]['eur_ex'] += eur_ex
        else:
            company_stats[r.company]['na_count'] += 1
        if eur_inc is not None:
            company_stats[r.company]['eur_inc'] += eur_inc
    for slug, stats in sorted(company_stats.items()):
        pretty = COMPANIES.get(slug, slug)
        ex_str = f'€{stats["eur_ex"]:,.2f}'
        inc_str = f'€{stats["eur_inc"]:,.2f}'
        notes = f'{stats["na_count"]} N/A' if stats['na_count'] > 0 else ''
        lines.append(
            f'| {pretty} | {stats["count"]} | {ex_str} | {inc_str} | {notes} |'
        )
    return lines


def write_summary(records: list[InvoiceRecord]):
    console.rule('[bold]Step 4: Writing summary')

    paid = [r for r in records if r.status == 'Paid']
    to_pay = [r for r in records if r.status == 'To-Pay']

    # Write SUMMARY.md
    lines = ['# Invoice Summary — March 2026\n']

    lines.append('## To Pay\n')
    if to_pay:
        lines.extend(_md_invoice_table(to_pay))
    else:
        lines.append('_No invoices to pay._')
    lines.append('')

    lines.append('## Paid\n')
    if paid:
        lines.extend(_md_invoice_table(paid))
    else:
        lines.append('_No paid invoices._')
    lines.append('')

    lines.append('## Totals\n')
    lines.append(f'- **Total invoices**: {len(records)}')
    lines.append(f'- **Paid**: {len(paid)}')
    lines.append(f'- **To-Pay**: {len(to_pay)}')

    if to_pay:
        lines.append('\n### To Pay — by company\n')
        lines.extend(_md_totals_table(to_pay))

    if paid:
        lines.append('\n### Paid — by company\n')
        lines.extend(_md_totals_table(paid))

    summary_text = '\n'.join(lines) + '\n'
    summary_path = BASE_DIR / 'SUMMARY.md'
    summary_path.write_text(summary_text)
    console.print(f'\nWritten to [bold]{summary_path}[/bold]')

    # Write invoices.json
    json_path = BASE_DIR / 'invoices.json'
    json_path.write_text(
        json.dumps([record_to_dict(r) for r in records], indent=2) + '\n'
    )
    console.print(f'Written to [bold]{json_path}[/bold]')

    # Rich console table
    table = Table(title='Invoice Summary', show_lines=True)
    table.add_column('#', style='dim', width=3)
    table.add_column('Type')
    table.add_column('From')
    table.add_column('PDF')
    table.add_column('Ex. VAT', style='green')
    table.add_column('Inc. VAT', style='green')
    table.add_column('EUR ex.', style='cyan')
    table.add_column('EUR inc.', style='cyan')
    table.add_column('Description')
    table.add_column('Status')
    table.add_column('Overdue')
    table.add_column('Company')

    for i, r in enumerate(records, 1):
        row_style = 'green' if r.status == 'Paid' else 'yellow'
        pretty_company = COMPANIES.get(r.company, r.company)
        eur_ex = convert_to_eur(r.amount_ex_vat, r.currency_code)
        eur_inc = convert_to_eur(r.amount_inc_vat, r.currency_code)
        overdue_str = '[red bold]OVERDUE[/red bold]' if r.is_overdue else ''
        table.add_row(
            str(i),
            r.doc_type.capitalize(),
            r.sender,
            r.renamed_pdf,
            _fmt_amount(r.amount_ex_vat),
            _fmt_amount(r.amount_inc_vat),
            _fmt_amount(eur_ex),
            _fmt_amount(eur_inc),
            r.summary,
            f'[{row_style}]{r.status}[/{row_style}]',
            overdue_str,
            pretty_company,
            style=row_style,
        )

    console.print(table)

    # Totals
    console.print(
        f'\n[bold]Total:[/bold] {len(records)} invoices — [green]{len(paid)} Paid[/green], [yellow]{len(to_pay)} To-Pay[/yellow]'
    )
