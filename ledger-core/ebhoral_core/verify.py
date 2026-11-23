"""Verify ledger: an independent check of the e-Bhoral ledger.

It does not trust the database's own functions. It re-reads every entry, recomputes every SHA-256 link in
Python, and compares the chain with (a) the anchors stored in the database and (b) digests that were emailed
out of the system and therefore cannot be rewritten by someone with database access.
"""
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Result:
    ok: bool = True
    entries_checked: int = 0
    findings: list = field(default_factory=list)

    def fail(self, msg):
        self.ok = False
        self.findings.append(msg)

    def text(self):
        head = "VERIFY LEDGER: PASS" if self.ok else "VERIFY LEDGER: FAIL"
        lines = [f"{head} ({self.entries_checked} entries checked)"]
        lines += [f"  - {f}" for f in self.findings] or ["  - chain intact, vouchers continuous, balances match"]
        return "\n".join(lines)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_ledger(conn, org_id, emailed_digests=()):
    """emailed_digests: iterable of dicts with 'anchor_date', 'last_seq', 'last_hash' taken from sent emails."""
    r = Result()
    code = conn.run("SELECT code FROM ebhoral.organisation WHERE org_id = :o", o=org_id)[0][0]
    rows = conn.run(
        "SELECT e.seq, e.voucher_no, e.store_id, e.item_id, e.qty_base::text, e.uom, e.qty_entered::text, "
        "e.factor_snapshot::text, e.prev_hash, e.entry_hash, t.txn_date FROM ebhoral.stock_ledger_entry e "
        "LEFT JOIN ebhoral.stock_txn t ON t.txn_id = e.txn_id WHERE e.org_id = :o ORDER BY e.seq", o=org_id)

    # 1. Chain: continuous sequence, correct links, content matches its hash.
    expected_prev, expected_seq, hash_at = _sha("ebhoral-genesis|" + code), 1, {0: _sha("ebhoral-genesis|" + code)}
    first_break = None
    for seq, voucher, store, item, qty, uom, qty_in, factor, prev, stored, posted in rows:
        r.entries_checked += 1
        if seq != expected_seq and first_break is None:
            first_break = f"entry #{expected_seq} is missing or out of order (found #{seq} next)"
        recomputed = _sha("|".join([prev, str(seq), voucher, str(store), str(item), qty, uom, qty_in, factor]))
        if prev != expected_prev and first_break is None:
            first_break = f"chain link broken at entry #{seq} ({voucher}): previous-hash does not match entry #{seq - 1}"
        if recomputed != stored and first_break is None:
            first_break = f"entry #{seq} ({voucher}, dated {posted}) was changed after posting: content no longer matches its hash"
        hash_at[seq] = stored
        expected_prev, expected_seq = stored, seq + 1
    if first_break:
        r.fail(first_break)

    # 2. Anchors stored in the database.
    for a_date, a_seq, a_hash in conn.run(
            "SELECT anchor_date, last_seq, last_hash FROM ebhoral.ledger_anchor WHERE org_id = :o ORDER BY anchor_date",
            o=org_id):
        if hash_at.get(a_seq) != a_hash:
            r.fail(f"ledger does not match the stored anchor of {a_date} (entry #{a_seq})")

    # 3. Digests emailed out of the system: the check an insider with database access cannot defeat.
    for d in emailed_digests:
        if hash_at.get(d["last_seq"]) != d["last_hash"]:
            r.fail(f"ledger does not match the digest emailed on {d['anchor_date']} "
                   f"(entry #{d['last_seq']}, sent hash {d['last_hash'][:16]}...): past entries were rewritten")

    # 4. Voucher continuity per series per financial year.
    for series, fy, nums in voucher_series_numbers(conn, org_id):
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if missing or len(nums) != len(set(nums)):
            r.fail(f"voucher series {series}/{fy} has gaps or duplicates (missing {missing[:5]})")

    # 5. Every balance equals the sum of its ledger lines.
    for store, item, bal, total in conn.run(
            "SELECT b.store_id, b.item_id, b.qty_base, coalesce(sum(e.qty_base), 0) FROM ebhoral.stock_balance b "
            "LEFT JOIN ebhoral.stock_ledger_entry e ON e.store_id = b.store_id AND e.item_id = b.item_id "
            "WHERE b.org_id = :o GROUP BY b.store_id, b.item_id, b.qty_base", o=org_id):
        if Decimal(bal) != Decimal(total):
            r.fail(f"balance of item {item} in store {store} is {bal} but its ledger lines sum to {total}")
    return r


def voucher_series_numbers(conn, org_id):
    out = {}
    for series, fy, voucher in conn.run(
            "SELECT series, fin_year, voucher_no FROM ebhoral.stock_txn WHERE org_id = :o", o=org_id):
        out.setdefault((series, fy), []).append(int(voucher.rsplit("/", 1)[1]))
    return [(s, fy, nums) for (s, fy), nums in sorted(out.items())]


def continuity_table(conn, org_id):
    rows = []
    for series, fy, nums in voucher_series_numbers(conn, org_id):
        gaps = len(set(range(1, max(nums) + 1)) - set(nums))
        rows.append({"series": series, "fin_year": fy, "first": min(nums), "last": max(nums),
                     "count": len(nums), "gaps": gaps, "duplicates": len(nums) - len(set(nums))})
    return rows
