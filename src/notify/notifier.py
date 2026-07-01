"""Notify CLI core — sends notifications to a list of recipients."""


def send(recipient):
    """Send a single notification and return a result string."""
    return f"sent to {recipient}"


def send_all(recipients):
    """Send a notification to each recipient, returning the list of results."""
    return [send(recipient) for recipient in recipients]
