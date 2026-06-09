from pathlib import Path

import src.invoices.audit as au


def test_log_and_read_roundtrip():
    au.log_event('alpha', {'x': 1})
    au.log_event('beta', {'y': 2})
    entries = au.read_audit_log()
    assert any(e['action'] == 'alpha' and e.get('x') == 1 for e in entries)
    assert all('time' in e for e in entries)


def test_limit_edges():
    au.log_event('gamma', {})
    assert len(au.read_audit_log(limit=1)) == 1
    assert au.read_audit_log(limit=0) == []
    assert au.read_audit_log(limit=-1) == []


def test_non_serializable_value_does_not_raise():
    au.log_event('delta', {'path': Path('/tmp/z')})  # must not raise
    assert any(e['action'] == 'delta' for e in au.read_audit_log())
