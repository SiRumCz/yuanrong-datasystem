# Notify CLI — Usage

The Notify CLI sends a notification to each recipient in a list.

## API

- `send(recipient)` — send a single notification; returns `"sent to <recipient>"`.
- `send_all(recipients)` — send a notification to each recipient; returns the list of results.

## Example

    from src.notify.notifier import send_all

    send_all(["alice", "bob"])
    # => ["sent to alice", "sent to bob"]
