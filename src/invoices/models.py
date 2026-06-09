from dataclasses import dataclass
from typing import TypedDict


@dataclass
class Attachment:
    message_id: str
    attachment_id: str
    filename: str


@dataclass
class Classification:
    doc_type: str  # "invoice", "receipt", "reminder", or "other"
    status: str
    slug: str
    amount_ex_vat: float | None
    amount_inc_vat: float | None
    summary: str
    reason: str
    filename: str
    currency_code: str
    is_overdue: bool
    doc_date: str = ''
    invoice_number: str | None = None
    beneficiary_name: str | None = None
    beneficiary_iban: str | None = None
    beneficiary_bic: str | None = None


@dataclass
class InvoiceRecord:
    subject: str
    sender: str
    renamed_pdf: str
    amount_ex_vat: float | None
    amount_inc_vat: float | None
    summary: str
    status: str
    company: str
    reason: str
    currency_code: str = 'EUR'
    doc_type: str = 'invoice'
    is_overdue: bool = False
    doc_date: str = ''
    thread_id: str = ''
    sha1: str = ''
    invoice_number: str | None = None
    beneficiary_name: str | None = None
    beneficiary_iban: str | None = None
    beneficiary_bic: str | None = None


@dataclass
class ChecklistItem:
    """One expected invoice from an accountant's missing-invoice list."""

    vendor: str
    description: str = ''
    amount: float | None = None
    currency: str = 'EUR'
    period: str = ''  # YYYY-MM (or YYYY-MM-DD), normalized where possible
    invoice_number: str | None = None
    raw: str = ''  # the original line/text this item came from


class InvoiceRecordDict(TypedDict):
    thread_id: str
    subject: str
    sender: str
    renamed_pdf: str
    amount_ex_vat: float | None
    amount_inc_vat: float | None
    summary: str
    status: str
    company: str
    reason: str
    currency_code: str
    doc_type: str
    is_overdue: bool
    doc_date: str
    sha1: str
    invoice_number: str | None
    beneficiary_name: str | None
    beneficiary_iban: str | None
    beneficiary_bic: str | None


def record_to_dict(r: InvoiceRecord) -> InvoiceRecordDict:
    return InvoiceRecordDict(
        thread_id=r.thread_id,
        subject=r.subject,
        sender=r.sender,
        renamed_pdf=r.renamed_pdf,
        amount_ex_vat=r.amount_ex_vat,
        amount_inc_vat=r.amount_inc_vat,
        summary=r.summary,
        status=r.status,
        company=r.company,
        reason=r.reason,
        currency_code=r.currency_code,
        doc_type=r.doc_type,
        is_overdue=r.is_overdue,
        doc_date=r.doc_date,
        sha1=r.sha1,
        invoice_number=r.invoice_number,
        beneficiary_name=r.beneficiary_name,
        beneficiary_iban=r.beneficiary_iban,
        beneficiary_bic=r.beneficiary_bic,
    )
