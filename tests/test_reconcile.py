import asyncio

import pytest

import src.invoices.reconcile as rec
from src.invoices.models import ChecklistItem


def rec_dict(**kw):
    base = dict(
        thread_id='',
        subject='',
        sender='',
        renamed_pdf='',
        amount_ex_vat=None,
        amount_inc_vat=None,
        summary='',
        status='To-Pay',
        company='my-company',
        reason='',
        currency_code='EUR',
        doc_type='invoice',
        is_overdue=False,
        doc_date='',
        sha1='',
        invoice_number=None,
        beneficiary_name=None,
        beneficiary_iban=None,
        beneficiary_bic=None,
    )
    base.update(kw)
    return base


class _FakeProc:
    def __init__(self, out):
        self.returncode = 0
        self.stdout_text = out
        self.stderr_text = ''


def test_parse_missing_list(monkeypatch):
    out = (
        'here you go {"items":[{"vendor":"AWS","amount":120.0,"period":"2026-03",'
        '"invoice_number":"INV-5","description":"hosting","currency":"EUR","raw":"x"},'
        '{"vendor":"","raw":"skip"}]}'
    )

    async def fake(cmd, **kwargs):
        return _FakeProc(out)

    monkeypatch.setattr(rec, 'async_run', fake)
    items = asyncio.run(rec.parse_missing_list('AWS 120 March'))
    assert len(items) == 1
    assert items[0].vendor == 'AWS'
    assert items[0].amount == 120.0
    assert items[0].period == '2026-03'


def test_parse_missing_list_empty():
    assert asyncio.run(rec.parse_missing_list('   ')) == []


def test_exact_invoice_amount_period_is_high():
    r = rec.reconcile(
        [
            ChecklistItem(
                vendor='AWS', amount=120.0, period='2026-03', invoice_number='INV-5'
            )
        ],
        [
            rec_dict(
                beneficiary_name='Amazon Web Services',
                amount_inc_vat=120.0,
                doc_date='2026-03-01',
                invoice_number='INV-5',
                renamed_pdf='aws.pdf',
            )
        ],
    )
    assert r['summary']['matched'] == 1
    assert r['matched'][0]['confidence_label'] == 'high'
    assert 'invoice number' in r['matched'][0]['matched_on']


def test_fuzzy_vendor_amount():
    r = rec.reconcile(
        [ChecklistItem(vendor='Beta Supplies', amount=500.0)],
        [rec_dict(beneficiary_name='Beta Supplies OÜ', amount_inc_vat=500.0)],
    )
    assert r['summary']['matched'] == 1
    assert 'vendor' in r['matched'][0]['matched_on']


def test_currency_mismatch_does_not_match():
    r = rec.reconcile(
        [ChecklistItem(vendor='Acme', amount=100.0, currency='USD')],
        [rec_dict(beneficiary_name='Zeta', amount_inc_vat=100.0, currency_code='EUR')],
    )
    assert r['summary']['matched'] == 0


def test_zero_and_none_amount_do_not_match():
    assert rec._amount_matches(0.0, 0.0) is False
    assert rec._amount_matches(None, 5.0) is False


def test_one_to_one_greedy():
    r = rec.reconcile(
        [
            ChecklistItem(vendor='A', invoice_number='1'),
            ChecklistItem(vendor='B', invoice_number='2'),
        ],
        [
            rec_dict(invoice_number='1', renamed_pdf='one.pdf'),
            rec_dict(invoice_number='2', renamed_pdf='two.pdf'),
        ],
    )
    assert r['summary']['matched'] == 2
    assert {m['invoice']['renamed_pdf'] for m in r['matched']} == {'one.pdf', 'two.pdf'}


def test_still_missing_and_unmatched():
    r = rec.reconcile(
        [ChecklistItem(vendor='Zeta Corp', amount=9.0)],
        [rec_dict(beneficiary_name='Acme', amount_inc_vat=1000.0, renamed_pdf='a.pdf')],
    )
    assert r['summary']['still_missing'] == 1
    assert r['summary']['unmatched_collected'] == 1


def test_item_from_dict_coerces_types():
    ci = rec.item_from_dict(
        {'vendor': 'Acme', 'amount': '1,000.00', 'invoice_number': 123, 'bogus': 'x'}
    )
    assert ci.amount == 1000.0
    assert ci.invoice_number == '123'


def test_item_from_dict_requires_vendor():
    with pytest.raises(ValueError):
        rec.item_from_dict({'amount': 5})


def test_extract_json_robust():
    assert (
        rec._extract_json('Sure! {"items":[{"vendor":"A {x}","raw":"} brace"}]}')
        is not None
    )
    assert rec._extract_json('use {like this}. Here: {"items":[{"vendor":"A"}]}')[
        'items'
    ]
    assert rec._extract_json('[{"vendor":"A"}]') == {'items': [{'vendor': 'A'}]}


def test_period_key_month_names():
    assert rec._period_key('January 2026') == '2026-01'
    assert rec._period_key('2026/3') == '2026-03'


def test_build_report():
    r = rec.reconcile([ChecklistItem(vendor='Ghost Vendor')], [])
    md = rec.build_report(r)
    assert 'Ghost Vendor' in md
    assert '## Still missing' in md
