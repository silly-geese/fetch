import os
import re
import tempfile
from pathlib import Path

from .config import BASE_DIR, COMPANIES, GMAIL_QUERY, STAGING_DIR, STATUSES
from .helpers import async_run, async_run_json, console


def create_directories():
    console.rule('[bold]Step 1: Creating directory structure')
    for status in STATUSES:
        for slug in COMPANIES:
            d = BASE_DIR / status / slug
            d.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    console.print(
        f'  Created directories under [bold]{BASE_DIR}[/bold] and [bold]{STAGING_DIR}[/bold]'
    )


async def search_messages(
    max_emails: int | None, query: str | None = None
) -> list[dict]:
    console.rule('[bold]Step 2: Searching Gmail for invoices')
    data = await async_run_json(
        [
            'gog',
            'gmail',
            'messages',
            'search',
            query or GMAIL_QUERY,
            '--json',
            '--all',
        ]
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

    console.print(f'  Found [bold]{len(message_dicts)}[/bold] messages to process')
    return message_dicts


async def fetch_message(message_id: str) -> dict:
    """Fetch full message metadata."""
    return await async_run_json(['gog', 'gmail', 'get', message_id, '--json'])


async def fetch_thread(thread_id: str) -> dict:
    """Fetch full thread with all messages."""
    data = await async_run_json(
        ['gog', 'gmail', 'thread', 'get', thread_id, '--full', '--json']
    )
    # gog wraps the result in a {"thread": ...} envelope
    if isinstance(data, dict) and 'thread' in data:
        return data['thread']
    return data


async def download_attachment(
    message_id: str, attachment_id: str, filename: str
) -> Path:
    """Download a single attachment into a per-message staging subfolder.

    ``filename`` comes from the email (attacker-influenced), so it is reduced to
    a bare basename and the download is namespaced under the (sanitized) message
    id — preventing path traversal and silent same-name collisions between items.
    """
    safe_name = Path(filename).name
    if not safe_name or safe_name in {'.', '..'}:
        raise ValueError(f'unsafe attachment filename: {filename!r}')
    safe_mid = re.sub(r'[^A-Za-z0-9_-]', '', message_id) or 'message'

    dest_dir = STAGING_DIR / safe_mid
    dest_dir.mkdir(parents=True, exist_ok=True)

    await async_run(
        [
            'gog',
            'gmail',
            'attachment',
            message_id,
            attachment_id,
            '--out',
            str(dest_dir),
            '--name',
            safe_name,
        ]
    )
    return dest_dir / safe_name


async def archive_thread(thread_id: str) -> None:
    """Remove INBOX label from a thread (archive it)."""
    await async_run(['gog', 'gmail', 'labels', 'modify', thread_id, '--remove=INBOX'])


async def create_draft_reply(
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

    cmd = ['gog', 'gmail', 'drafts', 'create']
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
