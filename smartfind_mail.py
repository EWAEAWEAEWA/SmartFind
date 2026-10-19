"""SMTP delivery for SmartFind match notifications."""
from email.message import EmailMessage
import smtplib
import ssl


def is_configured(settings):
    password = "".join(str(settings.get("smtp_password", "")).split())
    return all(str(settings.get(key, "")).strip()
               for key in ("smtp_host", "smtp_username")) and bool(password)


def send_match_email(notification, settings):
    """Send one notice; raise SMTP/network errors for the caller to record."""
    message = EmailMessage()
    message["Subject"] = f"SmartFind possible match: {notification['item']}"
    message["From"] = str(settings.get("from_email") or settings["smtp_username"]).strip()
    message["To"] = notification["recipient"]
    app_url = str(settings.get("app_url", "")).strip()
    review = f"\n\nReview your SmartFind inbox: {app_url}" if app_url else ""
    message.set_content(
        f"A possible match was found for your report: {notification['item']}\n"
        f"Rule-based similarity score: {notification['score']}/100.\n\n"
        "This is only a possible match, not proof of ownership. Please sign in to SmartFind "
        f"and review the report details.{review}\n"
    )

    host = str(settings["smtp_host"]).strip()
    port = int(settings.get("smtp_port", 587))
    timeout = int(settings.get("smtp_timeout", 20))
    password = "".join(str(settings["smtp_password"]).split())
    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(str(settings["smtp_username"]).strip(), password)
        server.send_message(message)
