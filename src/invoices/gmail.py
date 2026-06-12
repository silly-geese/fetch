from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .config import GMAIL_QUERY
from .helpers import async_run, async_run_json
from .providers import MailProvider

_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')


def _safe_id(value: str, kind: str) -> str:
    """Validate a Gmail message/thread/attachment id before it becomes a CLI arg.

    Gmail ids are base64url-ish and always start with an alphanumeric. Rejecting
    anything else stops a crafted value (e.g. one starting with '-') from being
    read as a flag by gog, closing argument injection.
    """
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f'unsafe {kind}: {value!r}')
    return value


class GmailProvider(MailProvider):
    """Google-hosted mailbox (gmail.com or any Workspace domain) via the gog CLI."""

    def _gog(self, *args: str) -> list[str]:
        cmd = ['gog']
        if self.address:
            cmd += ['-a', self.address]
        cmd.extend(args)
        return cmd

    async def search_messages(
        self, max_emails: int | None, query: str | None = None
    ) -> list[dict]:
        data = await async_run_json(
            self._gog(
                'gmail', 'messages', 'search', query or GMAIL_QUERY, '--json', '--all'
            )
        )

        # The output may be a list of messages or have a "messages" key
        if isinstance(data, dict):
            messages = data.get('messages', data.get('results', []))
        else:
            messages = data

        message_dicts = []
        for msg in messages:
            mid = msg.get('id') or msg.get('messageId')
            tid = msg.get('threadId', '')
            if mid:
                message_dicts.append({'id': mid, 'threadId': tid})

        if max_emails is not None:
            message_dicts = message_dicts[:max_emails]

        return message_dicts

    async def fetch_message(self, message_id: str) -> dict:
        """Fetch full message metadata."""
        message_id = _safe_id(message_id, 'message id')
        return await async_run_json(self._gog('gmail', 'get', message_id, '--json'))

    async def fetch_thread(self, thread_id: str) -> dict:
        """Fetch full thread with all messages."""
        thread_id = _safe_id(thread_id, 'thread id')
        data = await async_run_json(
            self._gog('gmail', 'thread', 'get', thread_id, '--full', '--json')
        )
        # gog wraps the result in a {"thread": ...} envelope
        if isinstance(data, dict) and 'thread' in data:
            return data['thread']
        return data

    async def download_attachment(
        self, message_id: str, attachment_id: str, filename: str
    ) -> Path:
        """Download a single attachment into the sanitized staging subfolder."""
        message_id = _safe_id(message_id, 'message id')
        attachment_id = _safe_id(attachment_id, 'attachment id')
        dest_dir, safe_name = self._staging_target(message_id, filename)

        await async_run(
            self._gog(
                'gmail',
                'attachment',
                message_id,
                attachment_id,
                '--out',
                str(dest_dir),
                '--name',
                safe_name,
            )
        )
        return dest_dir / safe_name

    async def archive_thread(self, thread_id: str) -> None:
        """Remove INBOX label from a thread (archive it)."""
        thread_id = _safe_id(thread_id, 'thread id')
        await async_run(
            self._gog('gmail', 'labels', 'modify', thread_id, '--remove=INBOX')
        )

    async def create_draft_reply(
        self,
        body: str,
        attachments: list[str],
        reply_to_message_id: str | None = None,
        to: str | None = None,
        subject: str | None = None,
    ) -> dict:
        """Create a Gmail DRAFT (never auto-sends) replying with files attached.

        Pass ``reply_to_message_id`` (the accountant's message) or ``to``. Returns a
        status dict. Defaulting to a draft keeps a human in the loop before anything
        goes to an external recipient.
        """
        if not reply_to_message_id and not to:
            raise ValueError('provide reply_to_message_id or to')

        paths = [Path(a).expanduser() for a in attachments]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(f'attachment(s) not found: {", ".join(missing)}')

        cmd = self._gog('gmail', 'drafts', 'create')
        if reply_to_message_id:
            cmd += ['--reply-to-message-id', reply_to_message_id]
        if to:
            cmd += ['--to', to]
        if subject:
            cmd += ['--subject', subject]

        # Body via a temp file so multi-line content survives intact. The file is
        # created before the try, so the finally guarantees cleanup even if the
        # write itself fails.
        fd, name = tempfile.mkstemp(suffix='.txt', prefix='fetch-draft-')
        os.close(fd)
        body_file = Path(name)
        try:
            body_file.write_text(body)
            cmd += ['--body-file', str(body_file)]
            for p in paths:
                cmd += ['--attach', str(p)]
            proc = await async_run(cmd, check=False)
        finally:
            body_file.unlink(missing_ok=True)

        if proc.returncode != 0:
            raise RuntimeError(f'gog drafts create failed: {proc.stderr_text[:300]}')
        return {
            'status': 'drafted',
            'attachments': [str(p) for p in paths],
            'output': proc.stdout_text.strip()[:500],
        }
