from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from imap_tools import AND, OR, MailBox

from .config import INVOICE_KEYWORDS
from .providers import MailProvider

if TYPE_CHECKING:
    from pathlib import Path

    from imap_tools import MailMessage

    from .models import EmailAccount

# Snippet length passed to the classifier; full HTML emails can be huge
_BODY_LIMIT = 4000


class ImapProvider(MailProvider):
    """Universal fallback for mailboxes not hosted on Google.

    IMAP has no thread concept, so every message is treated as its own
    single-message thread (threadId == message UID). Search fetches full
    messages once and caches them, so classification and attachment
    downloads never touch the network again.
    """

    def __init__(self, account: EmailAccount):
        super().__init__(account)
        self._messages: dict[str, MailMessage] = {}

    def _login(self) -> MailBox:
        env = self.account.imap_password_env
        password = os.environ.get(env, '') if env else ''
        if not password:
            raise RuntimeError(
                f'IMAP password for {self.address} not found; '
                f'set imap.password_env in config.yml and export {env or "<VAR>"}'
            )
        mailbox = MailBox(self.account.imap_host, self.account.imap_port)
        return mailbox.login(
            self.account.imap_username or self.address,
            password,
            initial_folder=self.account.imap_folder,
        )

    def _search_sync(self, max_emails: int | None, query: str | None) -> list[dict]:
        # IMAP cannot evaluate Gmail search syntax; a custom query is used as a
        # plain text search, the default is the invoice keyword search.
        criteria = OR(text=[query]) if query else OR(text=INVOICE_KEYWORDS)
        refs: list[dict] = []
        with self._login() as mailbox:
            for msg in mailbox.fetch(criteria, mark_seen=False, reverse=True):
                has_pdf = any(
                    (att.filename or '').lower().endswith('.pdf')
                    for att in msg.attachments
                )
                if not has_pdf:
                    continue
                self._messages[msg.uid] = msg
                refs.append({'id': msg.uid, 'threadId': msg.uid})
                if max_emails is not None and len(refs) >= max_emails:
                    break
        return refs

    def _fetch_sync(self, message_id: str) -> MailMessage:
        with self._login() as mailbox:
            for msg in mailbox.fetch(AND(uid=message_id), mark_seen=False):
                return msg
        raise RuntimeError(f'Message {message_id} not found on {self.label}')

    async def _get_message(self, message_id: str) -> MailMessage:
        msg = self._messages.get(message_id)
        if msg is None:
            msg = await asyncio.to_thread(self._fetch_sync, message_id)
            self._messages[message_id] = msg
        return msg

    def _normalize(self, msg: MailMessage) -> dict:
        return {
            'id': msg.uid,
            'threadId': msg.uid,
            'headers': {
                'subject': msg.subject,
                'from': msg.from_,
                'date': str(msg.date or ''),
            },
            'body': (msg.text or msg.html or '')[:_BODY_LIMIT],
            'attachments': [
                {
                    'filename': att.filename,
                    'attachmentId': str(i),
                    'mimeType': att.content_type,
                }
                for i, att in enumerate(msg.attachments)
                if att.filename
            ],
        }

    async def search_messages(
        self, max_emails: int | None, query: str | None = None
    ) -> list[dict]:
        return await asyncio.to_thread(self._search_sync, max_emails, query)

    async def fetch_message(self, message_id: str) -> dict:
        return self._normalize(await self._get_message(message_id))

    async def fetch_thread(self, thread_id: str) -> dict:
        return {'messages': [await self.fetch_message(thread_id)]}

    async def download_attachment(
        self, message_id: str, attachment_id: str, filename: str
    ) -> Path:
        msg = await self._get_message(message_id)
        attachment = msg.attachments[int(attachment_id)]
        dest_dir, safe_name = self._staging_target(message_id, filename)
        path = dest_dir / safe_name
        path.write_bytes(attachment.payload)
        return path

    def _archive_sync(self, message_id: str) -> None:
        folder = self.account.imap_archive_folder
        with self._login() as mailbox:
            if not mailbox.folder.exists(folder):
                mailbox.folder.create(folder)
            mailbox.move(message_id, folder)

    async def archive_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self._archive_sync, thread_id)
