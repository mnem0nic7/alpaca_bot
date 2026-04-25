from __future__ import annotations

from alpaca_bot.notifications import LOG_ONLY, CompositeNotifier, Notifier
from alpaca_bot.notifications.email import EmailNotifier
from alpaca_bot.notifications.slack import SlackNotifier


def build_notifier(settings: object) -> Notifier:
    """Build a Notifier from Settings. Returns LOG_ONLY when no channels are configured."""
    active: list[Notifier] = []

    slack_url = getattr(settings, "slack_webhook_url", None)
    if slack_url:
        active.append(SlackNotifier(slack_url))

    smtp_host = getattr(settings, "notify_smtp_host", None)
    if smtp_host:
        active.append(
            EmailNotifier(
                smtp_host=smtp_host,
                smtp_port=getattr(settings, "notify_smtp_port", 587),
                smtp_user=getattr(settings, "notify_smtp_user", ""),
                smtp_password=getattr(settings, "notify_smtp_password", ""),
                from_addr=getattr(settings, "notify_email_from", ""),
                to_addr=getattr(settings, "notify_email_to", ""),
            )
        )

    if not active:
        return LOG_ONLY
    if len(active) == 1:
        return active[0]
    return CompositeNotifier(active)
