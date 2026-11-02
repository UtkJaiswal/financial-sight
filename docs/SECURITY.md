# Security and privacy

What FinSight does to protect personal financial data, and what it does not do yet. It holds bank statements, so read [What is not done](#what-is-not-done) before putting real data on anything but a trusted machine.

## Status

| Area | Status |
|---|---|
| Masking identifiers in display, classification and logs | Built |
| Encrypting originals, raw rows and clear values | Built, with locally generated keys |
| Audit log with a hash chain | Built |
| Gateway for external models | Built, off by default |
| Role checks on actions that change data or reveal values | Built, but roles are not authenticated |
| Sign-in, read restrictions, key management, retention | **Not built** |

## What is sensitive, and where it lives

| Data | Where | Protection |
|---|---|---|
| Original files | `data/files/<sha256>.bin` | Fernet encrypted |
| Raw rows | `bronze.raw_rows` | Fernet encrypted |
| Account numbers, holder names, clear narrations | `security.pii_vault` | Fernet encrypted, addressed by token |
| Masked narrations | `silver.transactions` | In clear in the database. Identifiers masked, but see below |
| Amounts, balances, dates | `silver.transactions`, `gold.snapshots` | **In clear in the database** |
| Filenames | `bronze.files` and report output | **In clear.** A filename can itself hold a name |
| Encryption keys | `data/dev_keys.json`, or environment variables | **In clear on disk in development.** Git-ignored |
| PDF passwords | Nowhere | Used for one read, then dropped |

The database file itself is not encrypted, and anyone holding `data/dev_keys.json` can decrypt everything.

## Masking

`security/pii.py` finds these without any model:

| Kind | How found | Display | For classification |
|---|---|---|---|
| Holder name | Names read from the statement header | `[Holder]` | `<HOLDER>` |
| Own account | A number matching an account in this case | `XXXXXX1234` | `<OWN_ACCT>` |
| Card | 16 digits passing the Luhn check | `XXXX…1234` | `<CARD>` |
| UPI ID, email | Patterns | `r***@okbank` | `<UPI>`, `<EMAIL>` |
| PAN | Pattern | `XXXXX1234X` | `<PAN>` |
| Aadhaar | Spaced pattern | Last 4 digits | `<AADHAAR>` |
| Phone | Indian mobile pattern | Last 4 digits | `<PHONE>` |
| Other long number | 9 to 19 digits | Last 4 digits | `<NUM>` |

Every narration in a case is masked again after each upload, because a new statement can reveal another own account or name.

### Known masking gaps

- **Other people's names are not masked, in any script.** Only the holder's name is known to the system. `IMPS/MOHAN VERMA` displays as printed, is stored in clear in `description` and `description_norm`, and passes the egress re-scan, which looks for identifiers rather than names. **Fix this before enabling any external model.**
- **Aadhaar without spaces** is treated as a long number. It is masked the same way but not labelled as Aadhaar.
- **Detection is by pattern**, so an unusual format can be missed.

## Tokens and encryption

| Mechanism | Detail |
|---|---|
| Tokens | `HMAC-SHA256(key, kind + normalised value)`, cut to 24 hex characters. Deterministic, so matching and de-duplication work on tokens, but they cannot be brute-forced without the key the way a plain hash of an account number can |
| Normalising | Unicode-aware. It keeps letters and marks in any script. An ASCII-only version once made different Indic narrations share one token. `test_tokens.py` guards this |
| Narrations | Hashed exactly, so two different narrations never share a token |
| Encryption | Fernet (AES-128-CBC with HMAC) |
| Keys | From `FINSIGHT_HMAC_KEY` and `FINSIGHT_FERNET_KEY`, otherwise generated into `data/dev_keys.json`. Production keys belong in a key management service |

**Losing the keys loses the data.** Changing the HMAC key changes tokens and IDs, so existing cases would no longer match new uploads.

## Revealing a clear value

`POST .../reveal` needs the `reveal` permission and a reason of at least 5 characters. The audit log records who, why and which row. It writes the log entry before returning the value. Only narrations can be revealed this way.

## Roles

| Permission | Analyst | Senior | Auditor | Admin |
|---|---|---|---|---|
| Create cases, upload, correct, treat, annotate, set rates, assign engines | Yes | Yes | | Yes |
| Reveal a clear narration | Yes | Yes | | Yes |
| View the audit log | | Yes | Yes | Yes |
| Create, edit and delete rule engines | | Yes | | Yes |

`promote_global` is defined for senior and admin, but no route uses it yet.

## Audit log

`review.audit_log` records case creation, uploads, rejections, duplicates, corrections, treatments, notes, reveals, rate changes and engine changes.

Each entry's hash covers the previous entry's hash and its own fields. `GET /api/audit` checks the whole chain and reports where it breaks.

Limits:

- The chain has no outside anchor. Someone able to rewrite the whole table could rebuild it.
- An override and its audit entry are written in two steps, so a crash between them leaves an unlogged override.
- Analyses triggered by a change are not logged separately. Snapshots record their trigger.

## External models

FinSight sends nothing outside by default. There is one route out, `security/egress.py`:

1. Refuses unless `FINSIGHT_EXTERNAL_MODELS=1`.
2. Re-scans the masked payload for identifiers, and blocks if it finds any. It does not send with a warning.
3. Logs every attempt to `security.egress_log`, including blocked ones.

`classify/jev_client.py` is the only caller. It also needs `JEV_API_URL` and `JEV_API_KEY`, and the request format is a placeholder. It has never run against a real API.

## Logs

`security/log_scrub.py` masks identifiers in every log record, including the root and uvicorn loggers. Unhandled errors return a plain message to the screen and keep the detail in the log.

The scrubber uses the same patterns as masking, so the same gaps apply. It does not know holder names, because it has no case context, so a name in an exception message would stay.

## Hostile files

| Control | Detail |
|---|---|
| Upload limits | Four extensions, 15 MB, not empty |
| Reader failures | Caught and reported as unreadable |
| Amount limits | Above 10¹³ rupees, an amount is refused |
| Date limits | Years outside 1990 to 2099 are not dates |
| Static file serving | Confined to `frontend/dist`, whatever the path says |
| CORS | Only the local dev server's origins |

`test_robustness.py` runs broken and odd files, wrong API calls and simultaneous uploads.

**Not protected:** PDF parsing runs in the server process with no sandbox, and there is no limit on page count or decompressed size, so a crafted file could use a lot of memory or time.

## What is not done

Needed before real use beyond a single trusted machine, in rough priority order:

| Gap | Risk | What is needed |
|---|---|---|
| No authentication | Anyone who can reach the port can claim any role by setting a header | Real sign-in, with roles from verified claims |
| No read restrictions | Every case, transaction and report can be read with no role check | Permission checks on reads, and per-case access |
| Server binds where it is told | Started with `--host 0.0.0.0`, all of the above is exposed | Keep to `127.0.0.1` until the above is done. There is no TLS, so put it behind a proxy that has it |
| Keys beside the data | A copy of `data/` is a copy of everything | A key management service, and key rotation (not built) |
| Database not encrypted | Amounts, balances and third-party names are readable from the file | Disk or database encryption |
| No retention or erasure | Nothing can delete a case or file through the app | Delete routes, a retention policy, and a decision on keeping audit entries |
| No backup | Losing `data/` loses every case | Backups, encrypted, with a tested restore |
| Third-party names unmasked | See above | Name detection, in every script |
| No rate limiting | Uploads and analysis are expensive, and one at a time | Limits per user |

The application has not had an independent security review. Demo data in this repository is generated.
