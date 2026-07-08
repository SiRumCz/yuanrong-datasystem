"""Notify CLI core — sends notifications to a list of recipients."""

# Module-level record of notifications actually sent (dry-run must not touch this).
_sent = []


def send(recipient):
    """Send a single notification, record it, and return a result string."""
    message = f"sent to {recipient}"
    _sent.append(message)
    return message


def send_all(recipients, dry_run=False):
    """Send a notification to each recipient, returning the list of results.

    In dry-run mode the messages are returned WITHOUT sending them (nothing is
    recorded in the send log).
    """
    if dry_run:
        return [f"sent to {recipient}" for recipient in recipients]
    return [send(recipient) for recipient in recipients]
