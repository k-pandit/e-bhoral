# e-Bhoral ledger core

**SYNTHETIC DATA.** This is the database core behind the e-Bhoral proposal for the NE Seva First Innovation Challenge 2026: a GFR-native stores ledger that is append-only, hash-chained and enforced inside PostgreSQL. Every rule claimed in proposal section 4.4 has an automated test here, and the test report is generated from a real run.

## What it proves

| Rule | Where | Test |
|---|---|---|
| One writer, one transaction | `sql/002_ledger.sql` `post_txn` | T1, T9 |
| Append-only, enforced by the database (grants + triggers) | `sql/003_roles.sql`, triggers in `002` | T4 |
| Corrections only by reversal, once, with reason and sanction | `post_reversal`, CHECK constraints | T6 |
| No negative stock, even under concurrency | `stock_balance` CHECK + per-organisation lock | T2, T3 |
| Supervisor-approved count correction posted with the issue | `post_count_correction_with_issue` | T8 |
| Gapless voucher numbers per financial year (no SEQUENCE) | `next_voucher_no` | T7 |
| SHA-256 hash chain | `entry_digest`, `post_txn` | T5 |
| Daily anchor that cannot be altered; tampering found by comparing with the emailed digest | `make_anchor`, `ebhoral_core/verify.py` | T5, T10 |
| Unit conversions snapshotted on every line | `factor_snapshot` | T11 |

**What it does not prove:** the user interface, hosting security (that needs a VAPT by a CERT-In-empanelled auditor), and email delivery. Tampering becomes **evident, not impossible**.

## Run it (Windows, Linux or macOS)

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

```bash
.venv/Scripts/python scripts/make_report.py
```

On Linux or macOS use `.venv/bin/...`. `pgserver` starts an embedded PostgreSQL 16 automatically, so no Docker or separate installation is needed. The script runs all tests and writes `reports/ledger-test-report.html`, plus a PDF if Edge or Chrome is installed.

To run only the tests: `.venv/Scripts/python -m pytest tests -q`.

## Layout

| Path | Content |
|---|---|
| `sql/001_schema.sql` | Tables and constraints |
| `sql/002_ledger.sql` | Posting functions, gapless numbering, hash chain, anchors, append-only triggers |
| `sql/003_roles.sql` | Application role: read, maintain the catalogue, post only through functions |
| `ebhoral_core/` | Database setup, posting wrappers, the independent `verify_ledger`, digest rendering |
| `tests/test_ledger.py` | Tests T1–T12 (pytest + Hypothesis) |
| `scripts/make_report.py` | Runs the tests and builds the report |

## How it fits the full system
In the pilot, Django 5.2 wraps these SQL files unchanged in migrations (`RunSQL`), and every screen posts through the same functions. The database, not the application code, is what guarantees the rules.
