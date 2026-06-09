import src.invoices.dropbox as dropbox_mod
from src.invoices.dropbox import copy_to_dropbox
from src.invoices.models import InvoiceRecord


def _record(**kw):
    base = dict(
        subject='Invoice 1',
        sender='vendor@example.com',
        renamed_pdf='inv1.pdf',
        amount_ex_vat=10.0,
        amount_inc_vat=12.0,
        summary='test',
        status='To-Pay',
        company='my-company',
        reason='test',
    )
    base.update(kw)
    return InvoiceRecord(**base)


def _seed_source(base_dir, rec):
    src_dir = base_dir / rec.status / rec.company
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / rec.renamed_pdf).write_bytes(b'%PDF-1.4 test')


def test_copies_into_any_folder_not_just_dropbox(tmp_path, monkeypatch):
    # A destination that is plainly not a Dropbox path: dropbox_dirs is just a
    # folder map, so a plain local/shared folder must work the same way.
    dest = tmp_path / 'Accounting' / 'My Company'
    dest.mkdir(parents=True)
    base_dir = tmp_path / 'output'
    monkeypatch.setattr(dropbox_mod, 'BASE_DIR', base_dir)
    monkeypatch.setattr(dropbox_mod, 'DROPBOX_DIRS', {'my-company': dest})

    rec = _record()
    _seed_source(base_dir, rec)

    res = copy_to_dropbox([rec])

    assert res['copied'] == 1
    assert res['skipped'] == 0
    assert res['errors'] == 0
    assert (dest / 'inv1.pdf').exists()


def test_skips_when_no_folder_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(dropbox_mod, 'BASE_DIR', tmp_path / 'output')
    monkeypatch.setattr(dropbox_mod, 'DROPBOX_DIRS', {})  # no mapping for company

    res = copy_to_dropbox([_record(company='unknown-co')])

    assert res['skipped'] == 1
    assert res['details'][0]['reason'] == 'no folder configured for company'


def test_skips_when_destination_missing(tmp_path, monkeypatch):
    base_dir = tmp_path / 'output'
    missing = tmp_path / 'does-not-exist'
    monkeypatch.setattr(dropbox_mod, 'BASE_DIR', base_dir)
    monkeypatch.setattr(dropbox_mod, 'DROPBOX_DIRS', {'my-company': missing})

    rec = _record()
    _seed_source(base_dir, rec)

    res = copy_to_dropbox([rec])

    assert res['skipped'] == 1
    assert 'destination folder does not exist' in res['details'][0]['reason']
