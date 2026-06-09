import asyncio
import hashlib
import json
from pathlib import Path

import httpx
from rich.console import Console

console = Console()


async def async_run(cmd: list[str], *, check=True) -> asyncio.subprocess.Process:
    """Run a command asynchronously and return the result."""
    console.log(f'[dim]$ {" ".join(cmd)}[/dim]')
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if check and proc.returncode != 0:
        raise RuntimeError(
            f'Command failed ({proc.returncode}): {" ".join(cmd)}\n{stderr.decode()}'
        )
    # Attach decoded output for convenience
    proc.stdout_text = stdout.decode()
    proc.stderr_text = stderr.decode()
    return proc


async def async_run_json(cmd: list[str]) -> dict | list:
    """Run a command expecting JSON output."""
    proc = await async_run(cmd)
    return json.loads(proc.stdout_text)


def sha1_file(path: Path) -> str:
    h = hashlib.sha1(usedforsecurity=False)
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


_rate_cache: dict[str, float] = {}


def fetch_exchange_rate(from_currency: str, to_currency: str = 'EUR') -> float | None:
    """Fetch exchange rate using hexarate API. Returns None on failure."""
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return 1.0
    cache_key = f'{from_currency}_{to_currency}'
    if cache_key in _rate_cache:
        return _rate_cache[cache_key]
    url = f'https://hexarate.paikama.co/api/rates/{from_currency}/{to_currency}/latest'
    try:
        resp = httpx.get(
            url, headers={'User-Agent': 'invoice-downloader/1.0'}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        rate = data.get('data', {}).get('mid')
        if rate is not None:
            _rate_cache[cache_key] = float(rate)
            return float(rate)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def convert_to_eur(amount: float | None, currency_code: str) -> float | None:
    """Convert amount to EUR. Returns None on failure."""
    if amount is None:
        return None
    rate = fetch_exchange_rate(currency_code, 'EUR')
    if rate is None:
        return None
    return round(amount * rate, 2)
