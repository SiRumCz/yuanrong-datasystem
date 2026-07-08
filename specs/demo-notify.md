# Notify CLI — Specification

The Notify CLI sends notifications to a list of recipients.

## Requirements

- R1: The notifier MUST send a notification for each recipient in the input list.
- R2: The notifier MUST support a dry-run mode that returns the messages without sending them.
