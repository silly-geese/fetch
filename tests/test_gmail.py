import asyncio

import pytest

import src.invoices.gmail as gm
from src.invoices.config import STAGING_DIR
from src.invoices.models import EmailAccount


class _FakeProc:
    returncode = 0
    stdout_text = 'Draft created'
    stderr_text = ''


def _provider(address: str = '') -> gm.GmailProvider:
    return gm.GmailProvider(EmailAccount(address=address))


def test_gog_command_default_account():
    assert _provider()._gog('gmail', 'get', 'm1') == ['gog', 'gmail', 'get', 'm1']


def test_gog_command_selects_account():
    cmd = _provider('me@work.com')._gog('gmail', 'get', 'm1')
    assert cmd == ['gog', '-a', 'me@work.com', 'gmail', 'get', 'm1']


def test_create_draft_reply_builds_command(monkeypatch, tmp_path):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    pdf = tmp_path / 'inv.pdf'
    pdf.write_bytes(b'%PDF')
    res = asyncio.run(
        _provider().create_draft_reply(
            body='hi\nthere', attachments=[str(pdf)], reply_to_message_id='m1'
        )
    )
    cmd = captured['cmd']
    assert cmd[:4] == ['gog', 'gmail', 'drafts', 'create']
    assert '--reply-to-message-id' in cmd and 'm1' in cmd
    assert '--attach' in cmd and str(pdf) in cmd
    assert '--body-file' in cmd
    assert res['status'] == 'drafted'


def test_create_draft_reply_targets_account(monkeypatch, tmp_path):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    pdf = tmp_path / 'inv.pdf'
    pdf.write_bytes(b'%PDF')
    asyncio.run(
        _provider('me@work.com').create_draft_reply(
            body='x', attachments=[str(pdf)], to='acct@firm.ee'
        )
    )
    assert captured['cmd'][:3] == ['gog', '-a', 'me@work.com']


def test_create_draft_reply_missing_attachment(monkeypatch):
    async def fake(cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            _provider().create_draft_reply(
                body='x', attachments=['/no/such.pdf'], to='a@b.c'
            )
        )


def test_create_draft_reply_requires_recipient():
    with pytest.raises(ValueError):
        asyncio.run(_provider().create_draft_reply(body='x', attachments=[]))


def test_download_attachment_sanitizes_filename(monkeypatch):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    p = asyncio.run(_provider().download_attachment('msg123', 'a1', '../../evil.pdf'))
    assert p.name == 'evil.pdf'
    assert STAGING_DIR in p.parents
    assert '../../evil.pdf' not in captured['cmd']


def test_download_attachment_namespaced_by_account(monkeypatch):
    async def fake(cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    p1 = asyncio.run(_provider('a@x.com').download_attachment('m1', 'a1', 'inv.pdf'))
    p2 = asyncio.run(_provider('b@y.com').download_attachment('m1', 'a1', 'inv.pdf'))
    assert p1 != p2
    assert 'a@x.com' in p1.parts and 'b@y.com' in p2.parts


def test_download_attachment_rejects_dotdot(monkeypatch):
    async def fake(cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    with pytest.raises(ValueError):
        asyncio.run(_provider().download_attachment('m1', 'a1', '..'))


def test_gmail_ids_reject_flag_like_values():
    # ids that could be read as CLI flags (argument injection) are refused
    provider = _provider()
    with pytest.raises(ValueError):
        asyncio.run(provider.download_attachment('-x', 'a1', 'inv.pdf'))
    with pytest.raises(ValueError):
        asyncio.run(provider.download_attachment('m1', '-y', 'inv.pdf'))
    with pytest.raises(ValueError):
        asyncio.run(provider.fetch_message('-o'))
    with pytest.raises(ValueError):
        asyncio.run(provider.archive_thread('--remove=INBOX'))
