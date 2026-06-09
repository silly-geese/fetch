import asyncio
from pathlib import Path

import src.invoices.classify as cl
from src.invoices.config import BASE_DIR, STAGING_DIR
from src.invoices.summary import _md_cell


def test_safe_filename_part():
    assert '/' not in cl._safe_filename_part('../../etc/passwd')
    assert cl._safe_filename_part('..') == ''
    assert cl._safe_filename_part('  ..foo..  ') == 'foo'
    assert cl._safe_filename_part('a/b\\c') == 'a b c'


class _FakeProc:
    returncode = 0
    stdout_text = (
        '{"document_type":"invoice","status":"To-Pay","slug":"my-company",'
        '"issuer":"../../../tmp/PWNED","doc_date":"2026-01-01",'
        '"doc_number":"../../evil","summary":"s","reason":"r","currency_code":"EUR"}'
    )
    stderr_text = ''


def test_classify_pdf_is_hardened(monkeypatch):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(cl, 'async_run', fake)
    res = asyncio.run(cl.classify_pdf(Path('/tmp/x.pdf'), 's', 's', 's', ''))
    assert res is not None
    # filename cannot escape a target directory
    assert '/' not in res.filename and '\\' not in res.filename
    assert (Path('/some/target') / res.filename).parent == Path('/some/target')
    # classifier no longer bypasses permissions and fences untrusted email data
    assert 'bypassPermissions' not in captured['cmd']
    assert any('UNTRUSTED_EMAIL_DATA' in str(a) for a in captured['cmd'])


def test_staging_is_under_output_dir():
    assert STAGING_DIR.parent == BASE_DIR


def test_md_cell_escapes():
    assert _md_cell('a|b') == r'a\|b'
    assert '\n' not in _md_cell('a\nb')
    assert '\r' not in _md_cell('a\rb')
