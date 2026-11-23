# e-Bhoral (ই-ভঁৰাল): The GFR Stores Register, Made Digital

Proposal for the **North-East Seva First Innovation Challenge 2026**, Stream 1.

- **Challenge:** Digital Inventory & Movement Tracking for Government Press and Stationery.
- **Challenge owner:** Directorate of Printing & Stationery, Government of Assam.
- **Applicant:** Jorhat Institute of Science and Technology (JIST), Jorhat, Assam.

> **Today the register proves the store issued. e-Bhoral proves the office received.**

All data in this repository is **synthetic**. No Directorate data has been used.

## Try the prototype

**Live demo:** https://k-pandit.github.io/e-bhoral/

It runs entirely in your browser and has five user roles:
- stock and goods receipt;
- requisition, approval and issue against a signed, numbered voucher;
- GFR-23 registers, low-stock alerts and the audit trail;
- a live tamper-detection demonstration: edit a past entry, then run **Verify ledger** and see it caught.

## Ledger core and test report

[`ledger-core/`](ledger-core/) is the PostgreSQL 16 database core behind the proposal:
- an append-only, hash-chained ledger;
- corrections only by reversal;
- gapless voucher numbers per financial year;
- no negative stock, even under concurrency.

**13 of 13 automated tests pass**, including four simulated insider attacks, all detected.

- Test report: [`docs/Annex_A_Ledger_Test_Report.pdf`](docs/Annex_A_Ledger_Test_Report.pdf)
- How to rerun the tests: [`ledger-core/README.md`](ledger-core/README.md)

## Licence

[AGPL-3.0](LICENSE). The Government of Assam also receives a perpetual, royalty-free licence. See proposal section 10.5.
