"""Thin wrappers over the database posting functions. All rules live in the database (sql/002_ledger.sql)."""
import json


def _post(conn, org, series, txn_type, date, posted_by, lines, approved_by=None,
          reason=None, sanction=None, client_uuid=None):
    rows = conn.run(
        "SELECT ebhoral.post_txn(:org, :series, :type, CAST(:d AS date), :by, :appr, :reason, :sanc, "
        "NULL, CAST(:uuid AS uuid), CAST(:lines AS jsonb))",
        org=org, series=series, type=txn_type, d=str(date), by=posted_by, appr=approved_by,
        reason=reason, sanc=sanction, uuid=client_uuid, lines=json.dumps(lines))
    return rows[0][0]


def init_org(conn, org_id, code, name):
    conn.run("SELECT ebhoral.init_organisation(:o, :c, :n)", o=org_id, c=code, n=name)


def add_store(conn, store_id, org_id, code, name):
    conn.run("INSERT INTO ebhoral.store VALUES (:s, :o, :c, :n)", s=store_id, o=org_id, c=code, n=name)


def add_item(conn, item_id, org_id, code, name, base_uom, units=None, verified=False, stores=()):
    conn.run("INSERT INTO ebhoral.item VALUES (:i, :o, :c, :n, :u)", i=item_id, o=org_id, c=code, n=name, u=base_uom)
    for uom, factor in {base_uom: 1, **(units or {})}.items():
        conn.run("INSERT INTO ebhoral.item_uom VALUES (:i, :u, :f)", i=item_id, u=uom, f=str(factor))
    for s in stores:
        conn.run("INSERT INTO ebhoral.stock_position_status VALUES (:o, :s, :i, :v)", o=org_id, s=s, i=item_id, v=verified)


def post_receipt(conn, org, date, store, item, uom, qty, posted_by="store.keeper", client_uuid=None):
    return _post(conn, org, "GRN", "RECEIPT", date, posted_by,
                 [{"store_id": store, "item_id": item, "uom": uom, "qty": str(qty)}], client_uuid=client_uuid)


def post_issue(conn, org, date, store, item, uom, qty, posted_by="store.keeper", approved_by="approving.officer",
               client_uuid=None):
    return _post(conn, org, "IV", "ISSUE", date, posted_by,
                 [{"store_id": store, "item_id": item, "uom": uom, "qty": str(-qty)}],
                 approved_by=approved_by, client_uuid=client_uuid)


def post_transfer(conn, org, date, from_store, to_store, item, uom, qty, posted_by="store.keeper",
                  approved_by="approving.officer"):
    return _post(conn, org, "TRF", "TRANSFER", date, posted_by,
                 [{"store_id": from_store, "item_id": item, "uom": uom, "qty": str(-qty)},
                  {"store_id": to_store, "item_id": item, "uom": uom, "qty": str(qty)}], approved_by=approved_by)


def post_adjustment(conn, org, date, store, item, uom, qty, reason, sanction, posted_by="store.keeper",
                    approved_by="approving.officer"):
    return _post(conn, org, "ADJ", "ADJUSTMENT", date, posted_by,
                 [{"store_id": store, "item_id": item, "uom": uom, "qty": str(qty)}],
                 approved_by=approved_by, reason=reason, sanction=sanction)


def post_reversal(conn, org, voucher_no, date, reason, sanction, posted_by="store.keeper",
                  approved_by="approving.officer"):
    return conn.run("SELECT ebhoral.post_reversal(:o, :v, CAST(:d AS date), :by, :appr, :r, :s)",
                    o=org, v=voucher_no, d=str(date), by=posted_by, appr=approved_by, r=reason, s=sanction)[0][0]


def post_count_correction_with_issue(conn, org, date, store, item, counted, issue_uom, issue_qty,
                                     requested_by="store.keeper", supervisor=None,
                                     issue_approved_by="approving.officer"):
    return conn.run(
        "SELECT ebhoral.post_count_correction_with_issue(:o, :s, :i, CAST(:c AS numeric), CAST(:d AS date), "
        ":req, :sup, :u, CAST(:q AS numeric), :appr)",
        o=org, s=store, i=item, c=str(counted), d=str(date), req=requested_by, sup=supervisor,
        u=issue_uom, q=str(issue_qty), appr=issue_approved_by)[0][0]


def balance(conn, store, item):
    rows = conn.run("SELECT qty_base FROM ebhoral.stock_balance WHERE store_id = :s AND item_id = :i", s=store, i=item)
    return rows[0][0] if rows else 0
