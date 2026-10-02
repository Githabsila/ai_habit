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


def test_error_monitoring_report_covers_server_and_device_js_errors(uid, clean_error_tables):
    from db import log_error, log_client_error

    log_error("morning_ping", Exception("boom"), uid)
    log_client_error(uid, "TypeError: x is null")
    log_client_error(uid, "TypeError: x is null")

    report = mod.build_error_monitoring_report()

    assert "Сервер" in report and "morning_ping: 1" in report
    assert "Устройства (JS)" in report
    assert "TypeError: x is null — 2×" in report
    assert "у 1 польз." in report


def test_error_monitoring_report_escapes_html_from_client_messages(uid, clean_error_tables):
    # Текст приходит с клиента, а сообщение уходит с parse_mode='HTML' —
    # без экранирования Telegram отклонит всё сообщение целиком.
    from db import log_client_error

    log_client_error(uid, "Cannot read <b>x</b> & <script>")

    report = mod.build_error_monitoring_report()

    assert "&lt;b&gt;x&lt;/b&gt; &amp; &lt;script&gt;" in report
    assert "<script>" not in report


def test_error_monitoring_report_says_all_clear_when_nothing_happened(clean_error_tables):
    report = mod.build_error_monitoring_report()
    assert report.count("за 24ч ошибок не было ✅") == 2


def test_error_monitoring_report_ignores_opaque_script_errors(uid, clean_error_tables):
    from db import log_client_error

    log_client_error(uid, "Script error.")

    report = mod.build_error_monitoring_report()

    assert "Script error." not in report
    assert report.count("за 24ч ошибок не было ✅") == 2


def test_stats_report_no_longer_includes_error_monitoring_line():
    report = mod.build_stats_report()
    assert "Мониторинг ошибок" not in report
