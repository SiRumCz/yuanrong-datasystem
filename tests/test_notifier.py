import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notify.notifier import send, send_all


def test_send_single():
    assert send("alice") == "sent to alice"


def test_send_all_sends_each_recipient():
    assert send_all(["alice", "bob"]) == ["sent to alice", "sent to bob"]
