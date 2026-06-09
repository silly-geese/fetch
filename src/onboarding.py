import os
import shutil
import subprocess
from pathlib import Path

import yaml

from src import fetch
from src.invoices.helpers import console

_CONFIG_PATH = (
    Path(os.environ['FETCH_CONFIG']).expanduser()
    if 'FETCH_CONFIG' in os.environ
    else Path(__file__).resolve().parents[1] / 'config.yml'
)
_REQUIRED_KEYS = ['companies', 'dropbox_dirs', 'debtor_accounts']


def check_prerequisites() -> dict:
    """Check runtime prerequisites and return a structured report.

    Returns ``{'ok': bool, 'checks': [{'name', 'ok', 'detail', 'hint'}, ...]}``.
    Shared by the ``onboarding`` CLI command and the MCP ``health_check`` tool.
    """
    checks: list[dict] = []

    # gog CLI — used for all Gmail access.
    gog_path = shutil.which('gog')
    checks.append(
        {
            'name': 'gog CLI',
            'ok': bool(gog_path),
            'detail': gog_path or 'not found on PATH',
            'hint': '' if gog_path else 'Install: brew install steipete/tap/gogcli',
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

    # Gmail authentication — only checkable if gog is present.
    if gog_path:
        result = subprocess.run(  # noqa: S603
            [gog_path, 'auth', 'list'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        authed = result.returncode == 0 and bool(result.stdout.strip())
        checks.append(
            {
                'name': 'Gmail authentication',
                'ok': authed,
                'detail': 'authenticated' if authed else 'no authenticated accounts',
                'hint': ''
                if authed
                else 'Run: gog auth credentials <client_secret.json> && gog auth add you@gmail.com',
            }
        )
    else:
        checks.append(
            {
                'name': 'Gmail authentication',
                'ok': False,
                'detail': 'skipped (gog CLI not found)',
                'hint': 'Install gog first',
            }
        )

    # config.yml — companies, Dropbox paths, debtor accounts.
    if not _CONFIG_PATH.exists():
        checks.append(
            {
                'name': 'config.yml',
                'ok': False,
                'detail': f'{_CONFIG_PATH} not found',
                'hint': 'Copy the template: cp config.example.yml config.yml',
            }
        )
    else:
        try:
            with _CONFIG_PATH.open() as f:
                cfg = yaml.safe_load(f) or {}
            missing = [k for k in _REQUIRED_KEYS if k not in cfg]
            if missing:
                checks.append(
                    {
                        'name': 'config.yml',
                        'ok': False,
                        'detail': f'missing keys: {", ".join(missing)}',
                        'hint': 'See config.example.yml for the expected shape',
                    }
                )
            else:
                checks.append(
                    {
                        'name': 'config.yml',
                        'ok': True,
                        'detail': 'valid',
                        'hint': '',
                    }
                )
        except yaml.YAMLError as exc:
            checks.append(
                {
                    'name': 'config.yml',
                    'ok': False,
                    'detail': f'invalid YAML: {exc}',
                    'hint': 'Fix the YAML syntax in config.yml',
                }
            )

    return {'ok': all(c['ok'] for c in checks), 'checks': checks}


@fetch.command()
def onboarding():
    """Check prerequisites and guide setup."""
    report = check_prerequisites()
    for c in report['checks']:
        mark = '[green]✓[/green]' if c['ok'] else '[red]✗[/red]'
        console.print(f'{mark} {c["name"]}: {c["detail"]}')
        if not c['ok'] and c['hint']:
            console.print(f'    [dim]{c["hint"]}[/dim]')

    console.print()
    if report['ok']:
        console.print('[bold green]All checks passed![/bold green]')
    else:
        console.print('[bold red]Some checks failed.[/bold red] See hints above.')
        raise SystemExit(1)
