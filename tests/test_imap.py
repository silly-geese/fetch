import asyncio
from types import SimpleNamespace

import pytest

from src.invoices.config import STAGING_DIR
from src.invoices.imap import ImapProvider
from src.invoices.models import EmailAccount


def _provider() -> ImapProvider:
    return ImapProvider(
        EmailAccount(
            address='u@elsewhere.com',
            provider='imap',
            imap_host='imap.elsewhere.com',
            imap_password_env='ELSEWHERE_PW',
        )
    )


def _msg(uid='7', filename='inv.pdf', payload=b'%PDF'):
    att = SimpleNamespace(
        filename=filename, payload=payload, content_type='application/pdf'
    )
    return SimpleNamespace(
        uid=uid,
        subject='Invoice 1',
        from_='vendor@x.com',
        date=None,
        text='hello',
        html='',
        attachments=[att],
    )


def test_normalize_shapes_message():
    norm = _provider()._normalize(_msg())
    assert norm['id'] == '7' and norm['threadId'] == '7'
    assert norm['headers']['subject'] == 'Invoice 1'
    assert norm['headers']['from'] == 'vendor@x.com'
    assert norm['body'] == 'hello'
    assert norm['attachments'] == [
        {'filename': 'inv.pdf', 'attachmentId': '0', 'mimeType': 'application/pdf'}
    ]


def test_fetch_thread_wraps_single_message():
    provider = _provider()
    provider._messages['7'] = _msg()
    thread = asyncio.run(provider.fetch_thread('7'))
    assert [m['id'] for m in thread['messages']] == ['7']


def test_download_attachment_sanitizes_and_namespaces():
    provider = _provider()
    provider._messages['7'] = _msg(filename='../../evil.pdf')
    path = asyncio.run(provider.download_attachment('7', '0', '../../evil.pdf'))
    assert path.name == 'evil.pdf'
    assert STAGING_DIR in path.parents
    assert 'u@elsewhere.com' in path.parts
    assert path.read_bytes() == b'%PDF'


def test_download_attachment_rejects_non_numeric_attachment_id():
    provider = _provider()
    provider._messages['7'] = _msg()
    with pytest.raises(ValueError):
        asyncio.run(provider.download_attachment('7', '0; rm -rf /', 'inv.pdf'))


def test_login_requires_password_env(monkeypatch):
    monkeypatch.delenv('ELSEWHERE_PW', raising=False)
    with pytest.raises(RuntimeError, match='IMAP password'):
        _provider()._login()
