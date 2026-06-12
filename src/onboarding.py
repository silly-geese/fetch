import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml
from imap_tools import MailBox

from src import fetch
from src.invoices.config import EMAIL_ACCOUNTS
from src.invoices.helpers import console

_CONFIG_PATH = (
    Path(os.environ['FETCH_CONFIG']).expanduser()
    if 'FETCH_CONFIG' in os.environ
    else Path(__file__).resolve().parents[1] / 'config.yml'
)
_REQUIRED_KEYS = ['default_slug', 'companies', 'dropbox_dirs', 'debtor_accounts']


def check_prerequisites() -> dict:
    """Check runtime prerequisites and return a structured report.

    Returns ``{'ok': bool, 'checks': [{'name', 'ok', 'detail', 'hint'}, ...],
    'warnings': [str, ...]}``. Warnings are advisory (e.g. a gmail-configured
    domain whose mail is not Google-hosted) and do not flip ``ok``.
    Shared by the ``onboarding`` CLI command and the MCP ``health_check`` tool.
    """
    checks: list[dict] = []
    warnings: list[str] = []

    gmail_accounts = [a for a in EMAIL_ACCOUNTS if a.provider == 'gmail']
    imap_accounts = [a for a in EMAIL_ACCOUNTS if a.provider == 'imap']

    # gog CLI — needed for Google-hosted mailboxes (and for draft replies).
    gog_path = shutil.which('gog')
    if gmail_accounts:
        checks.append(
            {
                'name': 'gog CLI',
                'ok': bool(gog_path),
                'detail': gog_path or 'not found on PATH',
                'hint': '' if gog_path else 'Install: brew install openclaw/tap/gogcli',
            }
        )

    # claude CLI — used to classify invoice PDFs.
    claude_path = shutil.which('claude')
    checks.append(
        {
            'name': 'claude CLI',
            'ok': bool(claude_path),
            'detail': claude_path or 'not found on PATH',
            'hint': ''
            if claude_path
            else 'Install Claude Code: https://docs.anthropic.com/en/docs/claude-code',
        }
    )

    # Gmail authentication, per configured account.
    if gmail_accounts:
        if gog_path:
            warnings.extend(_mx_warnings(gmail_accounts))
            warnings.extend(_multi_domain_notes(gmail_accounts))
            checks.extend(_gmail_auth_checks(gog_path, gmail_accounts))
        else:
            checks.append(
                {
                    'name': 'Gmail authentication',
                    'ok': False,
                    'detail': 'skipped (gog CLI not found)',
                    'hint': 'Install gog first',
                }
            )

    # IMAP accounts: config completeness + a real login test.
    checks.extend(_imap_checks(imap_accounts))

    # config.yml — companies, destination folders, debtor accounts.
    checks.append(_config_check())

    return {
        'ok': all(c['ok'] for c in checks),
        'checks': checks,
        'warnings': warnings,
    }


def _config_check() -> dict:
    if not _CONFIG_PATH.exists():
        return {
            'name': 'config.yml',
            'ok': False,
            'detail': f'{_CONFIG_PATH} not found',
            'hint': 'Copy the template: cp config.example.yml config.yml',
        }
    try:
        with _CONFIG_PATH.open() as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        return {
            'name': 'config.yml',
            'ok': False,
            'detail': f'invalid YAML: {exc}',
            'hint': 'Fix the YAML syntax in config.yml',
        }
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        return {
            'name': 'config.yml',
            'ok': False,
            'detail': f'missing keys: {", ".join(missing)}',
            'hint': 'See config.example.yml for the expected shape',
        }
    return {'name': 'config.yml', 'ok': True, 'detail': 'valid', 'hint': ''}


def _authed_addresses(gog_path: str) -> list[str] | None:
    """Return lowercase authenticated addresses from gog, or None on failure."""
    result = subprocess.run(  # noqa: S603
        [gog_path, 'auth', 'list', '--json'],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        accounts = json.loads(result.stdout).get('accounts', [])
    except (json.JSONDecodeError, AttributeError):
        return None
    addresses = []
    for entry in accounts:
        if isinstance(entry, str):
            addresses.append(entry.lower())
        elif isinstance(entry, dict):
            address = entry.get('email') or entry.get('account') or ''
            if address:
                addresses.append(address.lower())
    return addresses


def _gmail_auth_checks(gog_path: str, gmail_accounts: list) -> list[dict]:
    authed = _authed_addresses(gog_path)
    if authed is None:
        return [
            {
                'name': 'Gmail authentication',
                'ok': False,
                'detail': 'could not read gog auth state',
                'hint': 'Run: gog auth list',
            }
        ]

    expected = [a.address for a in gmail_accounts if a.address]
    if not expected:
        # No explicit accounts configured: any authenticated account will do
        return [
            {
                'name': 'Gmail authentication',
                'ok': bool(authed),
                'detail': 'authenticated' if authed else 'no authenticated accounts',
                'hint': ''
                if authed
                else 'Run: gog auth credentials <client_secret.json> && gog auth add you@gmail.com',
            }
        ]

    return [
        {
            'name': f'Gmail account {addr}',
            'ok': addr.lower() in authed,
            'detail': 'authenticated'
            if addr.lower() in authed
            else 'not authenticated',
            'hint': '' if addr.lower() in authed else f'Run: gog auth add {addr}',
        }
        for addr in expected
    ]


def _mx_warnings(gmail_accounts: list) -> list[str]:
    """Warn when a gmail-configured domain's mail is not actually Google-hosted."""
    dig_path = shutil.which('dig')
    if not dig_path:
        return []
    warnings = []
    domains = sorted(
        {a.address.split('@', 1)[1] for a in gmail_accounts if '@' in a.address}
    )
    for domain in domains:
        result = subprocess.run(  # noqa: S603
            [dig_path, '+short', 'mx', domain],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        mx = result.stdout.strip()
        if result.returncode != 0 or not mx:
            continue  # lookup failed; not a config problem
        if 'google' not in mx.lower():
            warnings.append(
                f'{domain} mail is not Google-hosted; '
                f"use 'provider: imap' for this account in config.yml"
            )
    return warnings


def _multi_domain_notes(gmail_accounts: list) -> list[str]:
    domains = {a.address.split('@', 1)[1] for a in gmail_accounts if '@' in a.address}
    if len(domains) > 1:
        return [
            'Gmail accounts span multiple Google Workspace domains. The OAuth '
            'client must be "External" (not "Internal"), and while unverified '
            'each address must be added as a test user. A Workspace admin of '
            'each domain may also need to allow the client.'
        ]
    return []


def _imap_checks(imap_accounts: list) -> list[dict]:
    checks = []
    for account in imap_accounts:
        label = account.address or account.imap_host or 'imap account'
        problems = []
        if not account.address:
            problems.append('missing address')
        if not account.imap_host:
            problems.append('missing imap.host')
        if not account.imap_password_env:
            problems.append('missing imap.password_env')
        elif not os.environ.get(account.imap_password_env):
            problems.append(f'env var {account.imap_password_env} not set')

        if problems:
            checks.append(
                {
                    'name': f'IMAP {label}',
                    'ok': False,
                    'detail': ', '.join(problems),
                    'hint': 'Set address, imap.host, and imap.password_env in '
                    'config.yml, and export the password env var',
                }
            )
            continue

        try:
            mailbox = MailBox(account.imap_host, account.imap_port, timeout=15)
            mailbox.login(
                account.imap_username or account.address,
                os.environ[account.imap_password_env],
                initial_folder=account.imap_folder,
            )
            mailbox.logout()
            checks.append(
                {'name': f'IMAP {label}', 'ok': True, 'detail': 'login OK', 'hint': ''}
            )
        except Exception as exc:
            checks.append(
                {
                    'name': f'IMAP {label}',
                    'ok': False,
                    'detail': f'login failed: {exc}',
                    'hint': 'Check host/username/password; for providers with '
                    'two-factor auth, use an app password',
                }
            )
    return checks


@fetch.command()
def onboarding():
    """Check prerequisites and guide setup."""
    report = check_prerequisites()
    for c in report['checks']:
        mark = '[green]✓[/green]' if c['ok'] else '[red]✗[/red]'
        console.print(f'{mark} {c["name"]}: {c["detail"]}')
        if not c['ok'] and c['hint']:
            console.print(f'    [dim]{c["hint"]}[/dim]')
    for w in report['warnings']:
        console.print(f'[yellow]![/yellow] {w}')

    console.print()
    if report['ok']:
        console.print('[bold green]All checks passed![/bold green]')
    else:
        console.print('[bold red]Some checks failed.[/bold red] See hints above.')
        raise SystemExit(1)
