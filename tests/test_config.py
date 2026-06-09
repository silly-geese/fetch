import pytest

import src.invoices.config as cfg


def test_require_config_ok_when_loaded():
    # conftest points FETCH_CONFIG at a valid fixture, so config loaded cleanly.
    assert cfg.CONFIG_ERROR is None
    cfg.require_config()  # must not raise


def test_require_config_raises_when_missing(monkeypatch):
    monkeypatch.setattr(cfg, 'CONFIG_ERROR', 'config.yml missing (test)')
    with pytest.raises(RuntimeError, match='config'):
        cfg.require_config()
