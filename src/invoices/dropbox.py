import shutil

from .config import BASE_DIR, DROPBOX_DIRS
from .helpers import console
from .models import InvoiceRecord


def copy_to_dropbox(records: list[InvoiceRecord]) -> dict:
    """Copy classified invoice PDFs into their Dropbox company folders.

    Returns a summary dict: counts plus a per-file ``details`` list, so callers
    (CLI or MCP) can report exactly what happened.
    """
    console.rule('[bold]Step 5: Copying invoices to Dropbox')
    copied = 0
    skipped = 0
    errors = 0
    details: list[dict] = []

    for r in records:
        dropbox_dir = DROPBOX_DIRS.get(r.company)
        if dropbox_dir is None:
            console.print(
                f"  [yellow]SKIP:[/yellow] No Dropbox mapping for company '{r.company}'"
            )
            skipped += 1
            details.append(
                {
                    'pdf': r.renamed_pdf,
                    'company': r.company,
                    'status': 'skipped',
                    'reason': 'no Dropbox mapping for company',
                }
            )
            continue

        src = BASE_DIR / r.status / r.company / r.renamed_pdf
        if not src.exists():
            console.print(f'  [yellow]WARNING:[/yellow] PDF not found, skipping: {src}')
            skipped += 1
            details.append(
                {
                    'pdf': r.renamed_pdf,
                    'company': r.company,
                    'status': 'skipped',
                    'reason': f'source PDF not found: {src}',
                }
            )
            continue

        if not dropbox_dir.exists():
            console.print(
                f'  [yellow]SKIP:[/yellow] Dropbox dir does not exist: {dropbox_dir}'
            )
            skipped += 1
            details.append(
                {
                    'pdf': r.renamed_pdf,
                    'company': r.company,
                    'status': 'skipped',
                    'reason': f'Dropbox dir does not exist: {dropbox_dir}',
                }
            )
            continue

        dest = dropbox_dir / r.renamed_pdf
        try:
            shutil.copy2(str(src), str(dest))
            console.print(f'  [green]COPIED:[/green] {r.renamed_pdf} → {dropbox_dir}')
            copied += 1
            details.append(
                {
                    'pdf': r.renamed_pdf,
                    'company': r.company,
                    'status': 'copied',
                    'dest': str(dest),
                }
            )
        except OSError as exc:
            console.print(f'  [red]ERROR:[/red] Failed to copy {r.renamed_pdf}: {exc}')
            errors += 1
            details.append(
                {
                    'pdf': r.renamed_pdf,
                    'company': r.company,
                    'status': 'error',
                    'reason': str(exc),
                }
            )

    console.print(
        f'\n[bold]Dropbox copy:[/bold] {copied} copied, {skipped} skipped, {errors} errors'
    )
    return {
        'copied': copied,
        'skipped': skipped,
        'errors': errors,
        'details': details,
    }
