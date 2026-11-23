"""Daily integrity digest: the one-page text the evening job emails to the Director, Accounts and the audit cell.

Emailing is outside the scope of the ledger core; this module records the anchor and renders the digest text.
"""


def make_anchor(conn, org_id, date):
    import json
    raw = conn.run("SELECT ebhoral.make_anchor(:o, CAST(:d AS date))::text", o=org_id, d=str(date))[0][0]
    return json.loads(raw)


def render(anchor, org_name, recipients=("Director", "Accounts Officer", "Audit cell")):
    ranges = anchor["voucher_ranges"] or {}
    lines = [
        f"e-Bhoral daily integrity digest - {org_name}",
        f"Date: {anchor['anchor_date']}",
        f"Ledger entries to date: {anchor['last_seq']}",
        f"Chain hash (first 16): {anchor['last_hash'][:16]}",
        "Voucher series (last number issued):",
    ]
    lines += [f"  {k}: 000001 to {v:06d}" for k, v in sorted(ranges.items())] or ["  (none)"]
    lines += [
        f"Gap check: {anchor['gap_check']}",
        f"Sent to: {', '.join(recipients)}",
        "Keep this email. 'Verify ledger' compares the ledger with it; any later change to a past entry will show.",
    ]
    return "\n".join(lines)
