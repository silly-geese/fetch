from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from .config import EMAIL_ACCOUNTS, STAGING_DIR
from .helpers import console

if TYPE_CHECKING:
    from .models import EmailAccount


class MailProvider(ABC):
    """One connected mailbox.

    Message, thread, and attachment IDs are scoped to the mailbox they came
    from — an ID from one account cannot be fetched through another.
    """

    def __init__(self, account: EmailAccount):
        self.account = account

    @property
    def address(self) -> str:
        return self.account.address

    @property
    def label(self) -> str:
        return self.address or 'default Gmail account'

    def _staging_target(self, message_id: str, filename: str) -> tuple[Path, str]:
        """Sanitized (directory, name) for a staged attachment download.

        ``filename`` comes from the email (attacker-influenced), so it is
        reduced to a bare basename, and the path is namespaced by account and
        (sanitized) message id — preventing path traversal, collisions between
        same-named attachments in concurrent threads, and collisions between
        accounts (IMAP UIDs are small integers that repeat across mailboxes).
        """
        safe_name = Path(filename).name
        if not safe_name or safe_name in {'.', '..'}:
            raise ValueError(f'unsafe attachment filename: {filename!r}')
        safe_account = (
            re.sub(r'[^A-Za-z0-9_.@-]', '', self.address).strip('.') or 'default'
        )
        safe_mid = re.sub(r'[^A-Za-z0-9_-]', '', message_id) or 'message'

        dest_dir = STAGING_DIR / safe_account / safe_mid
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir, safe_name

    @abstractmethod
    async def search_messages(
        self, max_emails: int | None, query: str | None = None
    ) -> list[dict]:
        """Return candidate invoice messages as [{'id': ..., 'threadId': ...}]."""

    @abstractmethod
    async def fetch_message(self, message_id: str) -> dict:
        """Return a normalized message: headers dict, body text, attachments list."""

    @abstractmethod
    async def fetch_thread(self, thread_id: str) -> dict:
        """Return {'messages': [...]} for the full conversation."""

    @abstractmethod
    async def download_attachment(
        self, message_id: str, attachment_id: str, filename: str
    ) -> Path:
        """Download one attachment into staging and return its path."""

    @abstractmethod
    async def archive_thread(self, thread_id: str) -> None:
        """Remove the thread from the inbox."""


def build_providers() -> list[MailProvider]:
    # Imported here because both modules subclass MailProvider from this file
    from .gmail import GmailProvider  # noqa: PLC0415
    from .imap import ImapProvider  # noqa: PLC0415

    classes = {'gmail': GmailProvider, 'imap': ImapProvider}
    providers: list[MailProvider] = []
    for account in EMAIL_ACCOUNTS:
        cls = classes.get(account.provider)
        if cls is None:
            console.print(
                f'  [yellow]WARNING:[/yellow] Unknown provider '
                f"'{account.provider}' for {account.address}, skipping"
            )
            continue
        providers.append(cls(account))
    return providers


async def search_all_accounts(
    providers: list[MailProvider], max_emails: int | None, query: str | None = None
) -> list[dict]:
    """Search every mailbox concurrently and merge the results.

    Each returned dict carries the provider it came from, since all
    downstream operations must go through the same account. One failing
    account is reported and skipped instead of aborting the run.
    """
    console.rule('[bold]Step 2: Searching mailboxes for invoices')

    results = await asyncio.gather(
        *(p.search_messages(max_emails, query) for p in providers),
        return_exceptions=True,
    )

    message_dicts: list[dict] = []
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            console.print(f'  [red]ERROR:[/red] {provider.label}: {result}')
            continue
        console.print(f'  {provider.label}: [bold]{len(result)}[/bold] message(s)')
        message_dicts.extend(
            {**md, 'account': provider.address, 'provider': provider} for md in result
        )

    if max_emails is not None:
        message_dicts = message_dicts[:max_emails]

    console.print(f'  Found [bold]{len(message_dicts)}[/bold] messages to process')
    return message_dicts
