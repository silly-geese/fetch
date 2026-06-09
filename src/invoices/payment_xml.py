import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .classify import bic_from_iban
from .models import InvoiceRecord

logger = logging.getLogger(__name__)

NS = 'urn:iso:std:iso:20022:tech:xsd:pain.001.001.03'


def fill_missing_bics(records: list[InvoiceRecord]) -> list[tuple[str, str]]:
    """Derive missing beneficiary BICs from Estonian IBANs, in place.

    Returns ``[(renamed_pdf, derived_bic), ...]`` for each record that was filled.
    """
    derived: list[tuple[str, str]] = []
    for r in records:
        if not r.beneficiary_bic and r.beneficiary_iban:
            bic = bic_from_iban(r.beneficiary_iban)
            if bic:
                r.beneficiary_bic = bic
                derived.append((r.renamed_pdf, bic))
    return derived


def incomplete_beneficiaries(
    records: list[InvoiceRecord],
) -> list[tuple[InvoiceRecord, list[str]]]:
    """Find records missing beneficiary details needed for a SEPA payment.

    Returns ``[(record, ['name', 'IBAN', 'BIC']), ...]`` listing what's missing.
    """
    out: list[tuple[InvoiceRecord, list[str]]] = []
    for r in records:
        missing = []
        if not r.beneficiary_name:
            missing.append('name')
        if not r.beneficiary_iban:
            missing.append('IBAN')
        if not r.beneficiary_bic:
            missing.append('BIC')
        if missing:
            out.append((r, missing))
    return out


def _trunc(text: str, max_len: int) -> str:
    return text[:max_len]


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def generate_payment_xml(
    records: list[InvoiceRecord],
    debtor_accounts: dict[str, dict[str, str]],
    output_dir: Path,
    execution_date: str | None = None,
) -> list[Path]:
    """Generate LHV pain.001.001.03 payment XML files grouped by debtor company.

    Returns list of written file paths.
    """
    exec_date = execution_date or date.today().isoformat()

    # Filter to payable records with complete beneficiary info
    payable = [
        r
        for r in records
        if r.amount_inc_vat
        and r.beneficiary_name
        and r.beneficiary_iban
        and r.beneficiary_bic
    ]

    if not payable:
        logger.warning('No payable records with complete beneficiary details found.')
        return []

    # Group by debtor company
    by_company: dict[str, list[InvoiceRecord]] = defaultdict(list)
    for r in payable:
        by_company[r.company].append(r)

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for slug, group in by_company.items():
        debtor = debtor_accounts.get(slug)
        if not debtor:
            logger.warning(
                "No debtor account configured for '%s', skipping %d invoice(s).",
                slug,
                len(group),
            )
            continue

        msg_id = _trunc(
            f'MW-{slug[:12]}-{exec_date.replace("-", "")}-{datetime.now().strftime("%H%M%S")}',
            35,
        )
        nb_of_txs = str(len(group))
        ctrl_sum = f'{sum(r.amount_inc_vat for r in group):.2f}'

        root = ET.Element('Document', xmlns=NS)
        initn = _sub(root, 'CstmrCdtTrfInitn')

        # Group Header
        grp_hdr = _sub(initn, 'GrpHdr')
        _sub(grp_hdr, 'MsgId', msg_id)
        _sub(grp_hdr, 'CreDtTm', datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
        _sub(grp_hdr, 'NbOfTxs', nb_of_txs)
        _sub(grp_hdr, 'CtrlSum', ctrl_sum)
        _sub(_sub(grp_hdr, 'InitgPty'), 'Nm', _trunc(debtor['name'], 70))

        # Payment Information
        pmt_inf = _sub(initn, 'PmtInf')
        _sub(pmt_inf, 'PmtInfId', msg_id)
        _sub(pmt_inf, 'PmtMtd', 'TRF')
        _sub(pmt_inf, 'NbOfTxs', nb_of_txs)
        _sub(pmt_inf, 'CtrlSum', ctrl_sum)

        pmt_tp_inf = _sub(pmt_inf, 'PmtTpInf')
        _sub(_sub(pmt_tp_inf, 'SvcLvl'), 'Cd', 'SEPA')

        _sub(pmt_inf, 'ReqdExctnDt', exec_date)
        _sub(_sub(pmt_inf, 'Dbtr'), 'Nm', _trunc(debtor['name'], 70))

        dbtr_acct = _sub(pmt_inf, 'DbtrAcct')
        _sub(_sub(dbtr_acct, 'Id'), 'IBAN', debtor['iban'])

        dbtr_agt = _sub(pmt_inf, 'DbtrAgt')
        _sub(_sub(dbtr_agt, 'FinInstnId'), 'BIC', debtor['bic'])

        _sub(pmt_inf, 'ChrgBr', 'SLEV')

        # Credit Transfer Transactions
        for i, rec in enumerate(group, 1):
            tx = _sub(pmt_inf, 'CdtTrfTxInf')

            pmt_id = _sub(tx, 'PmtId')
            _sub(pmt_id, 'EndToEndId', _trunc(f'{slug}-{exec_date}-{i}', 35))

            amt = _sub(tx, 'Amt')
            instd = _sub(amt, 'InstdAmt', f'{rec.amount_inc_vat:.2f}')
            instd.set('Ccy', rec.currency_code)

            cdtr_agt = _sub(tx, 'CdtrAgt')
            _sub(_sub(cdtr_agt, 'FinInstnId'), 'BIC', rec.beneficiary_bic)

            _sub(_sub(tx, 'Cdtr'), 'Nm', _trunc(rec.beneficiary_name, 70))

            cdtr_acct = _sub(tx, 'CdtrAcct')
            _sub(_sub(cdtr_acct, 'Id'), 'IBAN', rec.beneficiary_iban)

            rmt_inf = _sub(tx, 'RmtInf')
            _sub(
                rmt_inf,
                'Ustrd',
                _trunc(
                    f'Invoice {rec.invoice_number}'
                    if rec.invoice_number
                    else rec.renamed_pdf,
                    140,
                ),
            )

        # Write XML
        tree = ET.ElementTree(root)
        ET.indent(tree, space='  ')
        filename = f'payment-{slug}-{exec_date}-{int(datetime.now().timestamp())}.xml'
        out_path = output_dir / filename
        tree.write(out_path, xml_declaration=True, encoding='UTF-8')
        paths.append(out_path)

    return paths
