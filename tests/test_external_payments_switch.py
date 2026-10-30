"""Выключатель оплаты картой и криптой внутри приложения и бота (config.EXTERNAL_PAYMENTS_ENABLED, правила Telegram §6.2 и §7.4): выключено — только звёзды, но подтверждения оплат по ссылке с внешнего сайта принимаются."""
import pytest

import config
import pay_providers
import payments_ext
from db import add_user
from payments_ext import PayError


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "id")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "key")
    monkeypatch.setattr(config, "TRIBUTE_API_KEY", "key")


def test_when_the_switch_is_off_only_stars_are_offered_even_with_keys_set(uid, configured, monkeypatch):
    add_user(uid, f"u{uid}", "Аня")
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", False)
    assert pay_providers.available_methods() == []
    assert [m["method"] for m in payments_ext.methods_for(uid, "subscription")] == ["stars"]


async def test_when_the_switch_is_off_an_external_order_cannot_be_created(uid, configured, monkeypatch):
    add_user(uid, f"u{uid}", "Аня")
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", False)
    for method in ("crypto", "card"):
        with pytest.raises(PayError) as caught:
            await payments_ext.start_order(uid, method, "subscription")
        assert caught.value.code == "method_unavailable"


def test_the_switch_does_not_touch_webhooks(configured, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", False)
    assert "heleket" in pay_providers.PROVIDERS and "tribute" in pay_providers.PROVIDERS, "приём подтверждений оплаты по ссылке остаётся"


def test_the_switch_is_off_by_default_in_the_code():
    source = open(config.__file__, encoding="utf-8").read()
    assert 'os.getenv("EXTERNAL_PAYMENTS_ENABLED", "false")' in source
