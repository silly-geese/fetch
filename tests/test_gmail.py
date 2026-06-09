import asyncio

import pytest

import src.invoices.gmail as gm
from src.invoices.config import STAGING_DIR


class _FakeProc:
    returncode = 0
    stdout_text = 'Draft created'
    stderr_text = ''


def test_create_draft_reply_builds_command(monkeypatch, tmp_path):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    pdf = tmp_path / 'inv.pdf'
    pdf.write_bytes(b'%PDF')
    res = asyncio.run(
        gm.create_draft_reply(
            body='hi\nthere', attachments=[str(pdf)], reply_to_message_id='m1'
        )
    )
    cmd = captured['cmd']
    assert cmd[:4] == ['gog', 'gmail', 'drafts', 'create']
    assert '--reply-to-message-id' in cmd and 'm1' in cmd
    assert '--attach' in cmd and str(pdf) in cmd
    assert '--body-file' in cmd
    assert res['status'] == 'drafted'


def test_create_draft_reply_missing_attachment(monkeypatch):
    async def fake(cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            gm.create_draft_reply(body='x', attachments=['/no/such.pdf'], to='a@b.c')
        )


def test_create_draft_reply_requires_recipient():
    with pytest.raises(ValueError):
        asyncio.run(gm.create_draft_reply(body='x', attachments=[]))


def test_download_attachment_sanitizes_filename(monkeypatch):
    captured = {}

    async def fake(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    p = asyncio.run(gm.download_attachment('m/../x!', 'a1', '../../evil.pdf'))
    assert p.name == 'evil.pdf'
    assert STAGING_DIR in p.parents
    assert '../../evil.pdf' not in captured['cmd']


def test_download_attachment_rejects_dotdot(monkeypatch):
    async def fake(cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(gm, 'async_run', fake)
    with pytest.raises(ValueError):
        asyncio.run(gm.download_attachment('m1', 'a1', '..'))
