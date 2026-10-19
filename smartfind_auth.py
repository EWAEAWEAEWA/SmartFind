"""Email verification and app-role resolution for the OIDC login flow."""


def resolve_role(email, email_verified, student_domains, admin_emails):
    """Return admin/student for an explicitly allowed, verified identity; else None."""
    normalized_email = str(email or "").strip().casefold()
    if not normalized_email or email_verified is not True or "@" not in normalized_email:
        return None

    admins = {str(value).strip().casefold() for value in admin_emails if str(value).strip()}
    if normalized_email in admins:
        return "admin"

    domain = normalized_email.rsplit("@", 1)[1]
    allowed_domains = {
        str(value).strip().lstrip("@").casefold()
        for value in student_domains
        if str(value).strip()
    }
    return "student" if domain in allowed_domains else None
