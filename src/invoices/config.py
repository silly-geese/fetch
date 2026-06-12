import os
from pathlib import Path

import yaml

from .models import EmailAccount

BASE_DIR = (
    Path(os.environ['FETCH_OUTPUT_DIR']).expanduser()
    if 'FETCH_OUTPUT_DIR' in os.environ
    else Path('./output')
)
# Staging lives under the user-scoped output dir, not a predictable world-writable
# /tmp path (avoids cross-user races / symlink games on shared machines).
STAGING_DIR = BASE_DIR / '.staging'

_CONFIG_PATH = (
    Path(os.environ['FETCH_CONFIG']).expanduser()
    if 'FETCH_CONFIG' in os.environ
    else Path(__file__).resolve().parents[2] / 'config.yml'
)
STATUSES = ['Paid', 'To-Pay']


# Load config.yml tolerantly. If it is missing or invalid, the package still
# imports (so the MCP server starts and health_check can report the problem) and
# only operations that actually need it raise, via require_config().
# VENDOR_SOURCES holds optional per-vendor retrieval recipes (portal URL / login
# hint / e-invoice operator), matched against checklist vendor names.
def _parse_email_accounts(cfg: dict) -> list[EmailAccount]:
    accounts: list[EmailAccount] = []
    for entry in cfg.get('email_accounts') or []:
        if isinstance(entry, str):
            accounts.append(EmailAccount(address=entry))
            continue
        imap = entry.get('imap') or {}
        accounts.append(
            EmailAccount(
                address=entry.get('address', ''),
                provider=entry.get('provider', 'gmail'),
                imap_host=imap.get('host', ''),
                imap_port=int(imap.get('port', 993)),
                imap_username=imap.get('username', ''),
                imap_password_env=imap.get('password_env', ''),
                imap_folder=imap.get('folder', 'INBOX'),
                imap_archive_folder=imap.get('archive_folder', 'Archive'),
            )
        )
    # No accounts configured: fall back to gog's default Gmail account
    return accounts or [EmailAccount(address='')]


CONFIG_ERROR: str | None = None
try:
    with _CONFIG_PATH.open() as f:
        _cfg = yaml.safe_load(f) or {}
    DEFAULT_SLUG = _cfg['default_slug']
    COMPANIES = _cfg['companies']
    DROPBOX_DIRS = {k: Path(v) for k, v in _cfg['dropbox_dirs'].items()}
    DEBTOR_ACCOUNTS = _cfg.get('debtor_accounts', {})
    VENDOR_SOURCES = _cfg.get('vendor_sources', {})
    EMAIL_ACCOUNTS = _parse_email_accounts(_cfg)
except (
    OSError,
    KeyError,
    TypeError,
    AttributeError,
    ValueError,
    yaml.YAMLError,
) as exc:
    CONFIG_ERROR = (
        f'config.yml could not be loaded from {_CONFIG_PATH} ({exc}). '
        'Create it from config.example.yml, or set FETCH_CONFIG to its path.'
    )
    DEFAULT_SLUG = 'my-company'
    COMPANIES = {}
    DROPBOX_DIRS = {}
    DEBTOR_ACCOUNTS = {}
    VENDOR_SOURCES = {}
    EMAIL_ACCOUNTS = [EmailAccount(address='')]


def require_config() -> None:
    """Raise a clear error if config.yml is missing or invalid.

    Called by operations that genuinely need config (fetch, dropbox, payments)
    so they fail fast with guidance instead of misbehaving on empty defaults.
    """
    if CONFIG_ERROR:
        raise RuntimeError(CONFIG_ERROR)


INVOICE_KEYWORDS = ['invoice', 'arve', 'receipt', 'payment', 'facture', 'paiement']

GMAIL_QUERY = (
    'in:inbox -category:(promotions OR social) '
    'has:attachment filename:pdf '
    f'({" OR ".join(INVOICE_KEYWORDS)})'
)


def build_classification_prompt() -> str:
    slug_lines = '\n'.join(
        f'   - "{slug}" ({name})' for slug, name in COMPANIES.items()
    )

    return f"""\
You are an invoice classifier. You will be given a PDF file, email metadata, and the full email conversation thread for context.

Determine:
1. DOCUMENT_TYPE: one of "invoice", "receipt", or "other".
   - "invoice": a proper invoice or credit note requesting or documenting payment for goods/services.
   - "receipt": a payment receipt or payment confirmation — proof that payment has already been made. NOT an invoice.
   - "reminder": a payment reminder, dunning letter, late-payment notice, or "Outstanding Account"
     statement that references an existing invoice but is NOT the invoice itself. If the PDF
     contains full invoice details (line items, amounts, payment info) classify it as "invoice"
     with is_overdue=true, not as "reminder".
   - "other": menus, contracts, work orders, articles of association, marketing material, or any non-financial document.

2. STATUS: "Paid" or "To-Pay". Only relevant when document_type is "invoice" or "receipt".
   Use your judgement based on the full context — the PDF contents, the email body, and the conversation thread.
   - "Paid" means the invoice has already been settled (e.g. the thread shows it was paid, or the document is a receipt).
   - "To-Pay" means the invoice is outstanding and payment is still expected. This is the default when unclear.
   - If document_type is "receipt", status should always be "Paid".
   Do NOT rely on superficial keyword matching. Read the document and thread holistically to understand the actual payment status.

3. IS_OVERDUE: true or false.
   Set to true if the email or thread indicates this is a reminder, follow-up, or escalation about a previously sent invoice that is past its due date. Look for language like "reminder", "overdue", "past due", "second notice", "still outstanding", "we have not received payment", or similar. Default to false.

4. SLUG: which company the invoice is addressed TO (the buyer/customer):
{slug_lines}
   Default to "{DEFAULT_SLUG}" if unclear.

5. AMOUNT_EX_VAT: total amount excluding VAT as a number (e.g. 1250.00). Use null if not determinable.

6. AMOUNT_INC_VAT: total amount including VAT as a number (e.g. 1500.00). Use null if not determinable.

7. SUMMARY: max ~10 words describing what the invoice is for.

8. REASON: brief explanation of your classification choices.

9. ISSUER: The name of the issuing company (the sender/vendor, not the recipient). Keep it short and recognizable.

10. DOC_DATE: The date the invoice/receipt was issued (i.e. the invoice date printed on the document), in YYYY-MM-DD format. This is NOT the due date, NOT the service/delivery date, and NOT the email date.

11. DOC_NUMBER: The invoice or receipt number as printed on the document. Use null if not found.

12. CURRENCY_CODE: ISO 4217 currency code (e.g. "EUR", "USD", "GBP"). Use the invoice's primary currency.

13. BENEFICIARY_NAME: The name of the payee/vendor as it appears on the invoice for bank transfer purposes. Use null if not determinable.

14. BENEFICIARY_IBAN: The IBAN (International Bank Account Number) of the payee/vendor as shown on the invoice. Use null if not found.

15. BENEFICIARY_BIC: The BIC/SWIFT code of the payee/vendor's bank as shown on the invoice. Use null if not found.

Reply with EXACTLY one JSON object (no extra text):
{{
  "document_type": "invoice",
  "status": "To-Pay",
  "is_overdue": false,
  "slug": "acme-corp-ou",
  "amount_ex_vat": 1250.00,
  "amount_inc_vat": 1500.00,
  "summary": "Cloud hosting services March 2026",
  "reason": "Invoice addressed to Acme Corp OÜ, no payment confirmation",
  "issuer": "Beta Supplies",
  "doc_date": "2026-03-01",
  "doc_number": "12345",
  "currency_code": "EUR",
  "beneficiary_name": "Beta Supplies OÜ",
  "beneficiary_iban": "EE382200221020145685",
  "beneficiary_bic": "HABAEE2X"
}}

Amounts must be numbers or null. Do not include currency symbols in amounts.
IBAN and BIC must be strings or null."""
