"""
Просьба пользователя: мониторинг ошибок за 24ч должен приходить отдельным
уведомлением ПОСЛЕ сообщения статистики, а не последней строкой внутри
него — иначе в длинной сводке его легко не заметить.
"""
import admin_digest_scheduler as mod


class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


async def test_daily_digest_sends_stats_and_errors_as_two_separate_messages(monkeypatch):
    monkeypatch.setattr(mod, "ADMIN_IDS", [111])
    monkeypatch.setattr(mod, "build_stats_report", lambda: "СТАТИСТИКА")
    monkeypatch.setattr(mod, "build_error_monitoring_report", lambda: "ОШИБКИ")

    bot = FakeBot()
    await mod.run_admin_daily_digest(bot)

    assert len(bot.sent) == 2
    assert bot.sent[0] == (111, "СТАТИСТИКА")
    assert bot.sent[1] == (111, "ОШИБКИ")


async def test_daily_digest_skips_error_message_if_stats_send_fails(monkeypatch):
    monkeypatch.setattr(mod, "ADMIN_IDS", [111])
    monkeypatch.setattr(mod, "build_stats_report", lambda: "СТАТИСТИКА")
    monkeypatch.setattr(mod, "build_error_monitoring_report", lambda: "ОШИБКИ")

    class FailingBot(FakeBot):
        async def send_message(self, chat_id, text=None, **kwargs):
            raise RuntimeError("заблокировал бота")

    bot = FailingBot()
    await mod.run_admin_daily_digest(bot)

    assert bot.sent == []


def test_error_monitoring_report_has_its_own_header():
    report = mod.build_error_monitoring_report()
    assert "Мониторинг ошибок" in report
    assert "Статистика бота" not in report


def test_stats_report_no_longer_includes_error_monitoring_line():
    report = mod.build_stats_report()
    assert "Мониторинг ошибок" not in report
