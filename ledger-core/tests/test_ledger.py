"""e-Bhoral ledger core tests. Each test ID (T1-T12) maps to a claim made in the proposal (section 4.4).
All data is SYNTHETIC."""
import datetime as dt
import random
import threading
import time
import uuid
from decimal import Decimal

import pg8000
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import MAIN, PRESS, ORG, save_artifact
from ebhoral_core import db, digest, posting
from ebhoral_core.verify import continuity_table, verify_ledger

D = dt.date(2026, 11, 20)
DB_ERRORS = (pg8000.exceptions.DatabaseError,)


def fails(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except DB_ERRORS as e:
        return str(e)
    return None


def sum_ledger(conn, store, item):
    return conn.run("SELECT coalesce(sum(qty_base), 0) FROM ebhoral.stock_ledger_entry "
                    "WHERE store_id = :s AND item_id = :i", s=store, i=item)[0][0]


# T1 ---------------------------------------------------------------------------------------------------------
def test_T1_balance_always_equals_ledger(app, org):
    """Balance always equals the sum of its ledger lines (10,000 random postings, 50 items, 2 stores)."""
    rng = random.Random(20261120)
    for i in range(10, 60):
        posting.add_item(app, i, ORG, f"SYN-{i:03d}", f"Synthetic item {i}", "piece", verified=True, stores=(MAIN, PRESS))
    ok = rejected = mismatches = 0
    for n in range(10_000):
        item, store = rng.randrange(10, 60), rng.choice((MAIN, PRESS))
        qty = rng.randint(1, 40)
        op = rng.random()
        if op < 0.45:
            err = fails(posting.post_receipt, app, ORG, D, store, item, "piece", qty)
        elif op < 0.9:
            err = fails(posting.post_issue, app, ORG, D, store, item, "piece", qty)
        else:
            err = fails(posting.post_transfer, app, ORG, D, store, MAIN + PRESS - store, item, "piece", qty)
        ok, rejected = (ok + 1, rejected) if err is None else (ok, rejected + 1)
        if (n + 1) % 1000 == 0:
            mismatches += app.run(
                "SELECT count(*) FROM ebhoral.stock_balance b WHERE b.qty_base <> (SELECT coalesce(sum(qty_base),0) "
                "FROM ebhoral.stock_ledger_entry e WHERE e.store_id = b.store_id AND e.item_id = b.item_id)")[0][0]
    minimum = app.run("SELECT min(qty_base) FROM ebhoral.stock_balance")[0][0]
    save_artifact("T1", {"attempted": 10_000, "posted": ok, "rejected_over_issues": rejected,
                         "balance_mismatches": mismatches, "minimum_balance": minimum})
    assert mismatches == 0
    assert minimum >= 0
    assert verify_ledger(app, ORG).ok


@settings(max_examples=40, derandomize=True, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.lists(st.tuples(st.sampled_from(["in", "out"]), st.integers(min_value=1, max_value=30)), max_size=25))
def test_T1b_property_any_sequence_keeps_invariants(template, ops):
    """Property test (Hypothesis): for ANY sequence of receipts and issues, the balance equals the ledger,
    never goes negative, and every rejected issue is one that would have overdrawn stock."""
    name = "ebhoral_h"
    db.create_database(name, template=template)
    c = db.connect(name, user="ebhoral_app")
    try:
        posting.init_org(c, ORG, "H", "hypothesis")
        posting.add_store(c, MAIN, ORG, "MAIN", "Main")
        posting.add_item(c, 1, ORG, "X", "X", "piece", verified=True, stores=(MAIN,))
        model = 0
        for kind, q in ops:
            if kind == "in":
                posting.post_receipt(c, ORG, D, MAIN, 1, "piece", q)
                model += q
            else:
                err = fails(posting.post_issue, c, ORG, D, MAIN, 1, "piece", q)
                assert (err is not None) == (q > model)
                if err is None:
                    model -= q
        assert Decimal(posting.balance(c, MAIN, 1)) == model == Decimal(sum_ledger(c, MAIN, 1))
        assert verify_ledger(c, ORG).ok
    finally:
        c.close()


# T2 ---------------------------------------------------------------------------------------------------------
def test_T2_no_negative_stock(app, org):
    """An issue larger than the balance is rejected and the balance is unchanged."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 3)          # 1,500 sheets
    err = fails(posting.post_issue, app, ORG, D, MAIN, 1, "ream", 4)
    save_artifact("T2", {"error": err, "balance_after": posting.balance(app, MAIN, 1)})
    assert err and "no_negative_stock" in err
    assert posting.balance(app, MAIN, 1) == 1500


# T3 ---------------------------------------------------------------------------------------------------------
def test_T3_simultaneous_last_stock(app, org, dbname):
    """20 simultaneous issues of the last 10 reams: exactly one succeeds and the balance ends at zero."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 10)
    results, barrier = [], threading.Barrier(20)

    def worker():
        c = db.connect(dbname, user="ebhoral_app")
        barrier.wait()
        results.append(fails(posting.post_issue, c, ORG, D, MAIN, 1, "ream", 10))
        c.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    succeeded = sum(1 for r in results if r is None)
    save_artifact("T3", {"attempts": 20, "succeeded": succeeded, "balance_after": posting.balance(app, MAIN, 1)})
    assert succeeded == 1
    assert posting.balance(app, MAIN, 1) == 0


# T4 ---------------------------------------------------------------------------------------------------------
def test_T4_append_only(app, org, superuser):
    """The application role cannot UPDATE, DELETE or TRUNCATE ledger rows; even the owner is stopped by a trigger."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 5)
    as_app = {
        "UPDATE": fails(app.run, "UPDATE ebhoral.stock_ledger_entry SET qty_base = 1"),
        "DELETE": fails(app.run, "DELETE FROM ebhoral.stock_ledger_entry"),
        "TRUNCATE": fails(app.run, "TRUNCATE ebhoral.stock_ledger_entry CASCADE"),
        "UPDATE stock_balance": fails(app.run, "UPDATE ebhoral.stock_balance SET qty_base = 99999"),
    }
    superuser.run("SET ROLE ebhoral_owner")
    as_owner = {
        "UPDATE": fails(superuser.run, "UPDATE ebhoral.stock_ledger_entry SET qty_base = 1"),
        "DELETE": fails(superuser.run, "DELETE FROM ebhoral.stock_txn"),
    }
    superuser.run("RESET ROLE")
    save_artifact("T4", {"as_application_role": as_app, "as_owner_role": as_owner})
    assert all(e and "permission denied" in e for e in as_app.values())
    assert all(e and "append-only" in e for e in as_owner.values())


# T5 ---------------------------------------------------------------------------------------------------------
def _ledger_with_digest(app):
    for i in range(30):
        posting.post_receipt(app, ORG, D, MAIN, 2, "bundle", 2)
        posting.post_issue(app, ORG, D, MAIN, 2, "piece", 40)
    anchor = digest.make_anchor(app, ORG, D)
    emailed = {"anchor_date": anchor["anchor_date"], "last_seq": anchor["last_seq"], "last_hash": anchor["last_hash"]}
    for i in range(5):                                            # next day's postings
        posting.post_receipt(app, ORG, D + dt.timedelta(days=1), MAIN, 2, "piece", 10)
    return emailed


def test_T5_tampering_is_evident(app, org, superuser):
    """A superuser with triggers switched off edits, deletes, swaps, and finally rewrites the whole chain.
    Verify ledger catches every case; the full rewrite is caught by the digest already emailed out."""
    emailed = _ledger_with_digest(app)
    assert verify_ledger(app, ORG, [emailed]).ok
    cases = {}

    def attack(sql_list, label):
        superuser.run("BEGIN")
        superuser.run("SET LOCAL session_replication_role = replica")     # disables triggers: insider attack
        for s in sql_list:
            superuser.run(s)
        res = verify_ledger(superuser, ORG, [emailed])
        superuser.run("ROLLBACK")
        cases[label] = res.text()
        return res

    r1 = attack(["UPDATE ebhoral.stock_ledger_entry SET qty_base = qty_base - 500, qty_entered = qty_entered - 10 "
                 "WHERE seq = 17"], "edit quantity of entry #17")
    r2 = attack(["DELETE FROM ebhoral.stock_ledger_entry WHERE seq = 23"], "delete entry #23")
    r3 = attack(["UPDATE ebhoral.stock_ledger_entry SET seq = -1 WHERE seq = 40",
                 "UPDATE ebhoral.stock_ledger_entry SET seq = 40 WHERE seq = 41",
                 "UPDATE ebhoral.stock_ledger_entry SET seq = 41 WHERE seq = -1"], "swap entries #40 and #41")
    # Full rewrite: edit #10, then recompute every later hash AND the stored anchors so the database is
    # internally consistent. Only the digest emailed out of the system can reveal it.
    rewrite = [
        "UPDATE ebhoral.stock_ledger_entry SET qty_base = qty_base + 1000, qty_entered = qty_entered + 20 WHERE seq = 10",
        """DO $$ DECLARE r record; prev text; h text; BEGIN
             SELECT entry_hash INTO prev FROM ebhoral.stock_ledger_entry WHERE seq = 9;
             FOR r IN SELECT * FROM ebhoral.stock_ledger_entry WHERE seq >= 10 ORDER BY seq LOOP
               h := ebhoral.entry_digest(prev, r.seq, r.voucher_no, r.store_id, r.item_id, r.qty_base, r.uom,
                                         r.qty_entered, r.factor_snapshot);
               UPDATE ebhoral.stock_ledger_entry SET prev_hash = prev, entry_hash = h WHERE seq = r.seq;
               prev := h;
             END LOOP;
             UPDATE ebhoral.ledger_anchor a SET last_hash = e.entry_hash FROM ebhoral.stock_ledger_entry e
              WHERE e.seq = a.last_seq;
             UPDATE ebhoral.stock_balance SET qty_base = qty_base + 1000 WHERE item_id = 2 AND store_id = 1;
           END $$""",
    ]
    r4 = attack(rewrite, "rewrite entry #10 and recompute the whole chain")
    save_artifact("T5", cases)
    assert not r1.ok and "#17" in r1.findings[0] and "changed after posting" in r1.findings[0]
    assert not r2.ok and "#23" in r2.findings[0]
    assert not r3.ok and "#40" in r3.findings[0]
    assert not r4.ok and any("digest emailed on" in f for f in r4.findings)
    assert verify_ledger(app, ORG, [emailed]).ok                         # after rollback, the real ledger is intact


# T6 ---------------------------------------------------------------------------------------------------------
def test_T6_corrections_only_by_reversal(app, org):
    """A reversal mirrors the original and nets to zero; it cannot be repeated, reversed, or posted without
    a reason and sanction reference."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 20)
    iv = posting.post_issue(app, ORG, D, MAIN, 1, "ream", 8)
    before = posting.balance(app, MAIN, 1)
    no_reason = fails(posting.post_reversal, app, ORG, iv, D, "", "")
    rev = posting.post_reversal(app, ORG, iv, D, "Wrong item issued", "DPNS/STORES/SYN/12")
    after = posting.balance(app, MAIN, 1)
    again = fails(posting.post_reversal, app, ORG, iv, D, "again", "X/1")
    rev_of_rev = fails(posting.post_reversal, app, ORG, rev, D, "undo", "X/2")
    lines = app.run("SELECT voucher_no, qty_base FROM ebhoral.stock_ledger_entry WHERE voucher_no IN (:a, :b) "
                    "ORDER BY seq", a=iv, b=rev)
    save_artifact("T6", {"issue": iv, "reversal": rev, "balance_before": before, "balance_after": after,
                         "lines": lines, "no_reason_error": no_reason, "second_reversal_error": again,
                         "reverse_a_reversal_error": rev_of_rev})
    assert no_reason and "adjustment_needs_reason_and_sanction" in no_reason
    assert after - before == 4000 and sum(q for _, q in lines) == 0
    assert again and "duplicate key" in again
    assert rev_of_rev and "cannot itself be reversed" in rev_of_rev
    assert app.run("SELECT count(*) FROM ebhoral.stock_txn WHERE voucher_no = :v", v=iv)[0][0] == 1  # original kept


# T7 ---------------------------------------------------------------------------------------------------------
def test_T7_gapless_voucher_numbers_per_fy(app, org, dbname):
    """2,000 postings with ~20% forced failures leave every series numbered 1..N with no gaps; the series
    restart at 000001 on 1 April; concurrent posting creates no duplicates."""
    rng = random.Random(7)
    forced = 0
    for _ in range(2000):
        if rng.random() < 0.2:
            forced += fails(posting.post_issue, app, ORG, D, PRESS, 1, "ream", 10_000) is not None
        else:
            posting.post_receipt(app, ORG, D, PRESS, 1, "ream", 1)
    mar31 = posting.post_receipt(app, ORG, dt.date(2027, 3, 31), MAIN, 1, "ream", 1)
    apr1 = posting.post_receipt(app, ORG, dt.date(2027, 4, 1), MAIN, 1, "ream", 1)
    apr1_issue = posting.post_issue(app, ORG, dt.date(2027, 4, 1), MAIN, 1, "ream", 1)

    def worker():
        c = db.connect(dbname, user="ebhoral_app")
        for _ in range(50):
            posting.post_receipt(c, ORG, D, MAIN, 2, "piece", 1)
        c.close()
    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    table = continuity_table(app, ORG)
    save_artifact("T7", {"forced_failures": forced, "mar31": mar31, "apr1": apr1, "apr1_issue": apr1_issue,
                         "continuity": table})
    assert forced > 300
    assert mar31.startswith("GRN/2026-27/") and apr1 == "GRN/2027-28/000001" and apr1_issue == "IV/2027-28/000001"
    assert all(r["gaps"] == 0 and r["duplicates"] == 0 for r in table)
    assert verify_ledger(app, ORG).ok


# T8 ---------------------------------------------------------------------------------------------------------
def test_T8_supervised_count_correction(app, org):
    """Unverified opening stock: the shelf count and the issue post together, only with a different supervisor.
    Verified stock cannot use this path."""
    posting.post_receipt(app, ORG, D, MAIN, 3, "piece", 5)                 # system says 5 service books
    plain = fails(posting.post_issue, app, ORG, D, MAIN, 3, "piece", 8)       # shelf actually has 9
    no_sup = fails(posting.post_count_correction_with_issue, app, ORG, D, MAIN, 3, 9, "piece", 8)
    same = fails(posting.post_count_correction_with_issue, app, ORG, D, MAIN, 3, 9, "piece", 8,
                 requested_by="store.keeper", supervisor="store.keeper")
    cc, iv = posting.post_count_correction_with_issue(app, ORG, D, MAIN, 3, 9, "piece", 8,
                                                      requested_by="store.keeper", supervisor="supdt.stores")
    task = app.run("SELECT system_qty, counted_qty, count_task_open FROM ebhoral.count_correction")[0]
    verified = fails(posting.post_count_correction_with_issue, app, ORG, D, MAIN, 1, 100, "piece", 1,
                     requested_by="store.keeper", supervisor="supdt.stores")
    save_artifact("T8", {"plain_issue_error": plain, "without_supervisor": no_sup, "same_person": same,
                         "posted": [cc, iv], "count_task": task, "balance_after": posting.balance(app, MAIN, 3),
                         "verified_item_error": verified})
    assert plain and no_sup and same
    assert cc.startswith("CC/") and iv.startswith("IV/")
    assert posting.balance(app, MAIN, 3) == 1 and task[2] is True
    assert verified and "use an ADJUSTMENT" in verified
    assert verify_ledger(app, ORG).ok


# T9 ---------------------------------------------------------------------------------------------------------
def test_T9_idempotent_posting(app, org, dbname):
    """The same submission (same client UUID) creates exactly one voucher, sequentially or concurrently."""
    u = str(uuid.uuid4())
    a = posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 1, client_uuid=u)
    b = posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 1, client_uuid=u)
    u2, got = str(uuid.uuid4()), []

    def worker():
        c = db.connect(dbname, user="ebhoral_app")
        got.append(posting.post_receipt(c, ORG, D, MAIN, 1, "ream", 1, client_uuid=u2))
        c.close()
    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    count = app.run("SELECT count(*) FROM ebhoral.stock_txn")[0][0]
    save_artifact("T9", {"sequential": [a, b], "concurrent": got, "vouchers_in_db": count})
    assert a == b and len(set(got)) == 1 and count == 2


# T10 --------------------------------------------------------------------------------------------------------
def test_T10_daily_digest(app, org, superuser):
    """The daily anchor records the chain head and voucher ranges, cannot be altered, and verify matches it."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 5)
    posting.post_issue(app, ORG, D, MAIN, 1, "ream", 2)
    anchor = digest.make_anchor(app, ORG, D)
    text = digest.render(anchor, "Directorate of Printing & Stationery (synthetic)")
    superuser.run("SET ROLE ebhoral_owner")
    altered = fails(superuser.run, "UPDATE ebhoral.ledger_anchor SET last_hash = 'x'")
    superuser.run("RESET ROLE")
    save_artifact("T10", {"anchor": anchor, "digest_text": text, "alter_error": altered})
    assert anchor["last_seq"] == 2 and anchor["gap_check"] == "OK"
    assert altered and "append-only" in altered
    assert verify_ledger(app, ORG, [anchor]).ok


# T11 --------------------------------------------------------------------------------------------------------
def test_T11_unit_snapshot(app, org):
    """Changing a unit conversion later never rewrites history or breaks the chain."""
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 4)                 # 4 x 500 = 2,000 sheets
    app.run("UPDATE ebhoral.item_uom SET factor_to_base = 480 WHERE item_id = 1 AND uom = 'ream'")
    posting.post_receipt(app, ORG, D, MAIN, 1, "ream", 1)                 # now 480 sheets
    lines = app.run("SELECT qty_entered, factor_snapshot, qty_base FROM ebhoral.stock_ledger_entry ORDER BY seq")
    save_artifact("T11", {"lines": lines})
    assert [tuple(map(Decimal, map(str, l))) for l in lines] == [(4, 500, 2000), (1, 480, 480)]
    assert verify_ledger(app, ORG).ok


# T12 --------------------------------------------------------------------------------------------------------
def test_T12_headroom(app, org):
    """Postings per second on this machine, against a pilot load of under 100 postings a day."""
    n, t0 = 1000, time.perf_counter()
    for _ in range(n):
        posting.post_receipt(app, ORG, D, MAIN, 2, "piece", 1)
    rate = n / (time.perf_counter() - t0)
    save_artifact("T12", {"postings": n, "per_second": round(rate, 1), "pilot_load_per_day": 100})
    assert rate > 10
