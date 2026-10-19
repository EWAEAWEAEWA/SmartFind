"""SMTP delivery for SmartFind match notifications."""
from email.message import EmailMessage
import smtplib
import ssl


class SMTPDeliveryError(RuntimeError):
    """An SMTP failure tagged with the stage where it happened."""


def _run_stage(stage, operation):
    try:
        return operation()
    except Exception as exc:
        raise SMTPDeliveryError(f"{stage}: {type(exc).__name__}: {exc}") from exc


def is_configured(settings):
    password = "".join(str(settings.get("smtp_password", "")).split())
    return all(str(settings.get(key, "")).strip()
               for key in ("smtp_host", "smtp_username")) and bool(password)


def _open_authenticated_smtp(settings):
    host = str(settings["smtp_host"]).strip()
    port = int(settings.get("smtp_port", 587))
    timeout = int(settings.get("smtp_timeout", 20))
    password = "".join(str(settings["smtp_password"]).split())
    server = _run_stage("connect", lambda: smtplib.SMTP(host, port, timeout=timeout))
    try:
        _run_stage("EHLO", server.ehlo)
        _run_stage("STARTTLS", lambda: server.starttls(context=ssl.create_default_context()))
        _run_stage("EHLO after STARTTLS", server.ehlo)
        _run_stage("authentication", lambda: server.login(
            str(settings["smtp_username"]).strip(), password))
    except Exception:
        _close_smtp(server)
        raise
    return server


def _close_smtp(server):
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            pass


def test_smtp_connection(settings):
    """Check connect/TLS/login without sending a message."""
    server = _open_authenticated_smtp(settings)
    try:
        _run_stage("NOOP probe", server.noop)
    finally:
        _close_smtp(server)


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
    server = _open_authenticated_smtp(settings)
    try:
        _run_stage("send message", lambda: server.send_message(message))
    finally:
        # A failed QUIT after a successful SMTP DATA response must not turn a delivered
        # email into a failed state that the user may resend as a duplicate.
        _close_smtp(server)
