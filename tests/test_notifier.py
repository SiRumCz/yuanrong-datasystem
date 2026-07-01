import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notify import notifier
from src.notify.notifier import send, send_all


def test_send_all_sends_each_recipient():
    notifier._sent.clear()
    assert send_all(["alice", "bob"]) == ["sent to alice", "sent to bob"]
    assert notifier._sent == ["sent to alice", "sent to bob"]


def test_dry_run_returns_without_sending():
    notifier._sent.clear()
    assert send_all(["alice"], dry_run=True) == ["sent to alice"]
    assert notifier._sent == []  # dry-run must not actually send
