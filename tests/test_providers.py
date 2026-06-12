import asyncio

import pytest

import src.invoices.providers as pr
import src.mcp_server as mcp
from src.invoices.config import _parse_email_accounts
from src.invoices.gmail import GmailProvider
from src.invoices.imap import ImapProvider
from src.invoices.models import EmailAccount


class _FakeProvider(pr.MailProvider):
    def __init__(self, address, result=None):
        super().__init__(EmailAccount(address=address))
        self._result = result or []

    async def search_messages(self, max_emails, query=None):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def fetch_message(self, message_id):
        return {}

    async def fetch_thread(self, thread_id):
        return {}

    async def download_attachment(self, message_id, attachment_id, filename):
        raise NotImplementedError

    async def archive_thread(self, thread_id):
        pass


# ── config parsing ───────────────────────────────────────────────────────────


def test_parse_email_accounts_defaults_to_gog_default():
    assert _parse_email_accounts({}) == [EmailAccount(address='')]


def test_parse_email_accounts_shapes():
    cfg = {
        'email_accounts': [
            'plain@example.com',
            {'address': 'g@work.com'},
            {
                'address': 'u@elsewhere.com',
                'provider': 'imap',
                'imap': {
                    'host': 'imap.elsewhere.com',
                    'port': 143,
                    'password_env': 'X_PW',
                },
            },
        ]
    }
    plain, gmail, imap = _parse_email_accounts(cfg)
    assert plain == EmailAccount(address='plain@example.com')
    assert gmail.provider == 'gmail' and gmail.address == 'g@work.com'
    assert imap.provider == 'imap'
    assert imap.imap_host == 'imap.elsewhere.com' and imap.imap_port == 143
    assert imap.imap_username == ''  # defaults to address at login time
    assert imap.imap_folder == 'INBOX' and imap.imap_archive_folder == 'Archive'


# ── provider construction ────────────────────────────────────────────────────


def test_build_providers_maps_types(monkeypatch):
    monkeypatch.setattr(
        pr,
        'EMAIL_ACCOUNTS',
        [
            EmailAccount(address='g@x.com'),
            EmailAccount(address='i@y.com', provider='imap'),
            EmailAccount(address='p@z.com', provider='pigeon'),  # unknown: skipped
        ],
    )
    providers = pr.build_providers()
    assert len(providers) == 2
    assert isinstance(providers[0], GmailProvider)
    assert isinstance(providers[1], ImapProvider)


# ── multi-account search ─────────────────────────────────────────────────────


def test_search_all_accounts_merges_and_tags():
    p1 = _FakeProvider('a@x.com', [{'id': '1', 'threadId': 't1'}])
    p2 = _FakeProvider('b@y.com', [{'id': '1', 'threadId': '1'}])
    msgs = asyncio.run(pr.search_all_accounts([p1, p2], None))
    assert len(msgs) == 2
    assert msgs[0]['account'] == 'a@x.com' and msgs[0]['provider'] is p1
    assert msgs[1]['account'] == 'b@y.com' and msgs[1]['provider'] is p2


def test_search_all_accounts_isolates_failures():
    p1 = _FakeProvider('a@x.com', RuntimeError('boom'))
    p2 = _FakeProvider('b@y.com', [{'id': '2', 'threadId': 't2'}])
    msgs = asyncio.run(pr.search_all_accounts([p1, p2], None))
    assert [m['id'] for m in msgs] == ['2']


def test_search_all_accounts_caps_total():
    p1 = _FakeProvider(
        'a@x.com', [{'id': str(i), 'threadId': str(i)} for i in range(3)]
    )
    p2 = _FakeProvider('b@y.com', [{'id': 'z', 'threadId': 'z'}])
    msgs = asyncio.run(pr.search_all_accounts([p1, p2], 2))
    assert len(msgs) == 2


# ── staging isolation ────────────────────────────────────────────────────────


def test_staging_target_is_namespaced_and_sanitized():
    d, name = _FakeProvider('a@x.com')._staging_target('42', '../../evil.pdf')
    assert name == 'evil.pdf'
    assert d.parts[-2:] == ('a@x.com', '42')
    # Same IMAP UID on two accounts cannot clobber each other
    d2, _ = _FakeProvider('b@y.com')._staging_target('42', 'evil.pdf')
    assert d != d2


def test_staging_target_rejects_bad_names():
    with pytest.raises(ValueError):
        _FakeProvider('a@x.com')._staging_target('42', '..')
    with pytest.raises(ValueError):
        _FakeProvider('a@x.com')._staging_target('42', '')


# ── MCP account routing ──────────────────────────────────────────────────────


def test_resolve_provider_single_account_needs_no_address():
    # The test config has no email_accounts, so there is exactly one provider
    provider = mcp._resolve_provider(None)
    assert isinstance(provider, GmailProvider) and provider.address == ''


def test_resolve_provider_multiple_accounts(monkeypatch):
    g1 = GmailProvider(EmailAccount(address='a@x.com'))
    g2 = GmailProvider(EmailAccount(address='b@y.com'))
    monkeypatch.setattr(mcp, 'build_providers', lambda: [g1, g2])
    with pytest.raises(ValueError, match='pass account='):
        mcp._resolve_provider(None)
    assert mcp._resolve_provider('a@x.com') is g1
    # Unknown address: falls back to a Gmail provider (gog may still be authed)
    fallback = mcp._resolve_provider('ghost@x.com')
    assert isinstance(fallback, GmailProvider) and fallback.address == 'ghost@x.com'


def test_resolve_gmail_provider_for_drafts(monkeypatch):
    gmail = GmailProvider(EmailAccount(address='a@x.com'))
    imap = ImapProvider(EmailAccount(address='i@y.com', provider='imap', imap_host='h'))
    monkeypatch.setattr(mcp, 'build_providers', lambda: [gmail, imap])
    # The lone Gmail account is picked even though two accounts exist
    assert mcp._resolve_gmail_provider(None) is gmail

    monkeypatch.setattr(mcp, 'build_providers', lambda: [imap])
    with pytest.raises(ValueError, match='No Gmail account'):
        mcp._resolve_gmail_provider(None)
