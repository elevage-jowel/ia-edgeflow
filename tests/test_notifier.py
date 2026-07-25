from unittest.mock import patch

from engine.notifier import NotificationConfig, Notifier


def test_disabled_by_default_sends_nothing():
    notifier = Notifier(NotificationConfig())
    with patch.object(notifier, "_send_telegram") as tg, patch.object(notifier, "_send_email") as em:
        notifier.notify("subject", "message")
    tg.assert_not_called()
    em.assert_not_called()


def test_telegram_only_sent_when_configured():
    cfg = NotificationConfig(telegram_bot_token="tok", telegram_chat_id="123")
    notifier = Notifier(cfg)
    with patch.object(notifier, "_send_telegram") as tg, patch.object(notifier, "_send_email") as em:
        notifier.notify("subject", "message")
    tg.assert_called_once_with("subject", "message")
    em.assert_not_called()


def test_both_channels_sent_when_both_configured():
    cfg = NotificationConfig(
        telegram_bot_token="tok", telegram_chat_id="123",
        smtp_host="smtp.example.com", email_to="me@example.com",
    )
    notifier = Notifier(cfg)
    with patch.object(notifier, "_send_telegram") as tg, patch.object(notifier, "_send_email") as em:
        notifier.notify("subject", "message")
    tg.assert_called_once()
    em.assert_called_once()


def test_throttling_suppresses_repeats_within_interval():
    cfg = NotificationConfig(telegram_bot_token="tok", telegram_chat_id="123")
    notifier = Notifier(cfg)
    with patch.object(notifier, "_send_telegram") as tg:
        notifier.notify("s1", "m1", kind="bug", min_interval_seconds=3600)
        notifier.notify("s2", "m2", kind="bug", min_interval_seconds=3600)
    tg.assert_called_once()


def test_throttling_is_independent_per_kind():
    cfg = NotificationConfig(telegram_bot_token="tok", telegram_chat_id="123")
    notifier = Notifier(cfg)
    with patch.object(notifier, "_send_telegram") as tg:
        notifier.notify("s1", "m1", kind="bug_a", min_interval_seconds=3600)
        notifier.notify("s2", "m2", kind="bug_b", min_interval_seconds=3600)
    assert tg.call_count == 2


def test_send_failure_is_swallowed_not_raised():
    cfg = NotificationConfig(telegram_bot_token="tok", telegram_chat_id="123")
    notifier = Notifier(cfg)
    with patch("engine.notifier.urllib_request.urlopen", side_effect=OSError("network down")):
        notifier.notify("subject", "message")  # must not raise
