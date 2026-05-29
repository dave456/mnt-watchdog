# GitHub Copilot Instructions

## Project Overview

This repository contains scripts for indi clients.

## Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them instead of picking silently.
- If a simpler solution exists, say so.
- If requirements are unclear, stop and ask a clarifying question.
- Deduplicate obvious repetition before finalizing:
    - If two or more callbacks/functions differ only by target widget, field name, or constant argument, merge into one parameterized helper.
    - Prefer passing context (attribute name, widget reference, enum/value) instead of creating one-off wrapper handlers.
- Mandatory self-review pass before returning code:
    - Scan for near-identical methods created during incremental implementation.
    - Collapse duplicates unless separation is required for clarity, threading, or future behavior divergence.
    - If duplicates are intentionally kept, state the reason explicitly.

## Simplicity First

**Write the minimum code that solves the requested problem. Nothing speculative.**

- Do not add code beyond the request.
- Avoid abstractions for one-time use.
- When porting code, keep the new code as close to the source implementation as practical to reduce future merge conflicts.

## Repository Structure

| File | Description |
|------|-------------|
| mnt-watchdog | Script that monitors the absolute position of a mount and checks the east and west maximum positions. If these positions are exceeded, it sends an abort request to the mount. |

## Python Script Conventions

### Imports and Dependencies

- For INDI clients, always import PyIndi (`import PyIndi`).
- The python API docuementation for PyIndi can be found here: https://docs.indilib.org/pyindi-client/

## License

All scripts are licensed under GPL-3.0.
