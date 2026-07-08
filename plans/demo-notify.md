# Notify CLI — Implementation Plan

This plan implements the Notify CLI specification.

## Plan items

- P1: Implement send_all(recipients) that calls send() once per recipient.
- P2: Implement dry-run mode in send_all(recipients, dry_run=False) that returns the messages without sending them.
