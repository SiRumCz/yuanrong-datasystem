# Notify CLI — Specification

The Notify CLI sends notifications to a list of recipients.

## Requirements

- R1: The notifier MUST send a notification for each recipient in the input list.
- R2: The notifier MUST write a timestamped audit log line for every notification sent.
