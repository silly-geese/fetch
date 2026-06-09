"""Plan retrieval of invoices that were NOT found in the inbox.

This is the host-agent-delegation version of the portal engine: instead of the
toolkit driving a browser itself (and holding credentials), it produces a
structured retrieval task per still-missing item — including any per-vendor
recipe from ``config.yml`` (``vendor_sources``) — and the host agent (which has
its own browser/web tools) fetches the file and attaches it to the draft reply.
Full autonomous portal automation can replace the execution step later.
"""

from __future__ import annotations

_DEFAULT_SOURCES = [
    "the vendor's own billing / account portal (log in and download the invoice)",
    "the company's e-invoice receiving platform (e.g. Envoice, or RIK "
    'e-arveldaja received e-invoices)',
    'asking the vendor to re-send the invoice by email',
]


def _match_source(vendor: str, vendor_sources: dict) -> dict | None:
    """Find the best per-vendor recipe whose key matches the vendor name.

    Substring match in either direction, but ignores keys shorter than 3 chars
    (so generic fragments like 'as'/'co'/'io' can't hijack a match) and prefers
    the longest matching key when several apply.
    """
    v = (vendor or '').lower().strip()
    if not v:
        return None
    best: tuple[int, str, dict] | None = None
    for key, cfg in vendor_sources.items():
        k = str(key).lower().strip()
        if len(k) < 3:
            continue
        if (k in v or v in k) and (best is None or len(k) > best[0]):
            best = (len(k), key, cfg if isinstance(cfg, dict) else {})
    if best is None:
        return None
    return {'matched_vendor': best[1], **best[2]}


def plan_retrieval(items: list[dict], vendor_sources: dict | None = None) -> list[dict]:
    """Build a retrieval task per still-missing checklist item."""
    vendor_sources = vendor_sources or {}
    tasks: list[dict] = []
    for it in items:
        vendor = (it.get('vendor') or '').strip()
        recipe = _match_source(vendor, vendor_sources)

        ident = ''
        if it.get('invoice_number'):
            ident += f' #{it["invoice_number"]}'
        if it.get('period'):
            ident += f' for {it["period"]}'

        portal = recipe.get('portal_url') if recipe else None
        portal = portal.strip() if isinstance(portal, str) and portal.strip() else None
        login_hint = recipe.get('login_hint') if recipe else None
        login_hint = (
            login_hint.strip()
            if isinstance(login_hint, str) and login_hint.strip()
            else None
        )

        if portal:
            sources = [portal]
            instructions = (
                f'Log in to {portal} and download the invoice{ident}'
                f'{" — " + login_hint if login_hint else ""}. '
                'Then attach the downloaded PDF to the accountant reply.'
            )
        else:
            sources = list(_DEFAULT_SOURCES)
            instructions = (
                f'Find the invoice{ident} from {vendor or "the vendor"} outside the '
                f'inbox: try {"; ".join(_DEFAULT_SOURCES)}. Download the PDF and '
                'attach it to the accountant reply.'
            )

        tasks.append(
            {
                'vendor': vendor,
                'invoice_number': it.get('invoice_number'),
                'period': it.get('period'),
                'amount': it.get('amount'),
                'recipe': recipe,
                'suggested_sources': sources,
                'instructions': instructions,
            }
        )
    return tasks
