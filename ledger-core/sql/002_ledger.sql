-- e-Bhoral ledger core: posting rules, enforced inside the database.
-- Run as ebhoral_owner. Posting functions are SECURITY DEFINER: the application role can call them
-- but cannot write to the tables directly.
SET search_path = ebhoral;

-- Indian financial year, April to March: 2027-03-31 -> '2026-27', 2027-04-01 -> '2027-28'.
CREATE FUNCTION fin_year(d date) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN extract(month FROM d) >= 4
    THEN extract(year FROM d)::int || '-' || lpad(((extract(year FROM d)::int + 1) % 100)::text, 2, '0')
    ELSE (extract(year FROM d)::int - 1) || '-' || lpad((extract(year FROM d)::int % 100)::text, 2, '0')
  END $$;

-- Next gapless voucher number. The UPDATE locks the counter row until the posting commits; if the
-- posting fails, the increment rolls back with it, so a series never has gaps.
CREATE FUNCTION next_voucher_no(p_org int, p_series text, p_date date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE fy text := fin_year(p_date); n int;
BEGIN
  INSERT INTO voucher_series (org_id, series, fin_year) VALUES (p_org, p_series, fy) ON CONFLICT DO NOTHING;
  UPDATE voucher_series SET last_no = last_no + 1
   WHERE org_id = p_org AND series = p_series AND fin_year = fy
  RETURNING last_no INTO n;
  RETURN p_series || '/' || fy || '/' || lpad(n::text, 6, '0');
END $$;

-- SHA-256 over an entry's content and the previous entry's hash. verify.py recomputes this independently.
CREATE FUNCTION entry_digest(p_prev text, p_seq bigint, p_voucher text, p_store int, p_item int,
                             p_qty numeric, p_uom text, p_qty_entered numeric, p_factor numeric)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws('|', p_prev, p_seq, p_voucher, p_store, p_item,
                p_qty::text, p_uom, p_qty_entered::text, p_factor::text), 'UTF8')), 'hex') $$;

CREATE FUNCTION genesis_hash(p_code text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to('ebhoral-genesis|' || p_code, 'UTF8')), 'hex') $$;

CREATE FUNCTION init_organisation(p_org int, p_code text, p_name text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ebhoral, pg_temp AS $$
BEGIN
  INSERT INTO organisation VALUES (p_org, p_code, p_name);
  INSERT INTO chain_head (org_id, last_seq, last_hash) VALUES (p_org, 0, genesis_hash(p_code));
END $$;

-- The single writer. One call = one voucher, posted completely or not at all.
-- p_lines: [{"store_id":1,"item_id":7,"uom":"ream","qty":-10,"factor":500?}]  (negative qty = out)
CREATE FUNCTION post_txn(p_org int, p_series text, p_type text, p_date date,
                         p_posted_by text, p_approved_by text, p_reason text, p_sanction text,
                         p_reverses bigint, p_client_uuid uuid, p_lines jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = ebhoral, pg_temp AS $$
DECLARE
  v_no text; v_txn bigint; v_existing text; ln jsonb;
  v_seq bigint; v_prev text; v_hash text; v_factor numeric; v_qty numeric; v_qty_base numeric;
BEGIN
  IF p_lines IS NULL OR jsonb_array_length(p_lines) = 0 THEN
    RAISE EXCEPTION 'a voucher needs at least one line';
  END IF;
  -- Serialise postings per organisation (keeps the hash chain linear).
  SELECT last_seq, last_hash INTO v_seq, v_prev FROM chain_head WHERE org_id = p_org FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown organisation %', p_org; END IF;
  -- Idempotency, checked after taking the lock so concurrent retries see the committed voucher.
  IF p_client_uuid IS NOT NULL THEN
    SELECT voucher_no INTO v_existing FROM stock_txn WHERE client_uuid = p_client_uuid;
    IF FOUND THEN RETURN v_existing; END IF;
  END IF;

  v_no := next_voucher_no(p_org, p_series, p_date);
  INSERT INTO stock_txn (org_id, voucher_no, series, fin_year, txn_type, txn_date, posted_by,
                         approved_by, reason, sanction_ref, reverses_txn_id, client_uuid)
  VALUES (p_org, v_no, p_series, fin_year(p_date), p_type, p_date, p_posted_by,
          p_approved_by, p_reason, p_sanction, p_reverses, p_client_uuid)
  RETURNING txn_id INTO v_txn;

  FOR ln IN SELECT value FROM jsonb_array_elements(p_lines) LOOP
    v_qty := (ln->>'qty')::numeric;
    v_factor := coalesce((ln->>'factor')::numeric,
                         (SELECT factor_to_base FROM item_uom
                           WHERE item_id = (ln->>'item_id')::int AND uom = ln->>'uom'));
    IF v_factor IS NULL THEN
      RAISE EXCEPTION 'unknown unit % for item %', ln->>'uom', ln->>'item_id';
    END IF;
    v_qty_base := v_qty * v_factor;
    v_seq := v_seq + 1;
    v_hash := entry_digest(v_prev, v_seq, v_no, (ln->>'store_id')::int, (ln->>'item_id')::int,
                           v_qty_base, ln->>'uom', v_qty, v_factor);
    INSERT INTO stock_ledger_entry VALUES (p_org, v_seq, v_txn, v_no, (ln->>'store_id')::int,
      (ln->>'item_id')::int, v_qty_base, ln->>'uom', v_qty, v_factor, v_prev, v_hash);
    -- The CHECK (qty_base >= 0) on stock_balance rejects any posting that would make stock negative.
    -- (UPDATE first: INSERT ... ON CONFLICT would test the CHECK on the proposed row before the conflict.)
    UPDATE stock_balance SET qty_base = qty_base + v_qty_base
     WHERE store_id = (ln->>'store_id')::int AND item_id = (ln->>'item_id')::int;
    IF NOT FOUND THEN
      INSERT INTO stock_balance (org_id, store_id, item_id, qty_base)
      VALUES (p_org, (ln->>'store_id')::int, (ln->>'item_id')::int, v_qty_base);
    END IF;
    v_prev := v_hash;
  END LOOP;

  UPDATE chain_head SET last_seq = v_seq, last_hash = v_prev WHERE org_id = p_org;
  RETURN v_no;
END $$;

-- Corrections only by reversal: mirrors the original line by line and links to it (once only).
CREATE FUNCTION post_reversal(p_org int, p_voucher text, p_date date, p_posted_by text,
                              p_approved_by text, p_reason text, p_sanction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = ebhoral, pg_temp AS $$
DECLARE v_txn bigint; v_type text; v_lines jsonb;
BEGIN
  SELECT txn_id, txn_type INTO v_txn, v_type FROM stock_txn WHERE org_id = p_org AND voucher_no = p_voucher;
  IF NOT FOUND THEN RAISE EXCEPTION 'voucher % not found', p_voucher; END IF;
  IF v_type = 'REVERSAL' THEN RAISE EXCEPTION 'a reversal cannot itself be reversed'; END IF;
  SELECT jsonb_agg(jsonb_build_object('store_id', store_id, 'item_id', item_id, 'uom', uom,
                                      'qty', -qty_entered, 'factor', factor_snapshot) ORDER BY seq)
    INTO v_lines FROM stock_ledger_entry WHERE txn_id = v_txn;
  RETURN post_txn(p_org, 'REV', 'REVERSAL', p_date, p_posted_by, p_approved_by,
                  p_reason, p_sanction, v_txn, NULL, v_lines);
END $$;

-- Supervisor-approved count correction, posted with the issue in ONE transaction.
-- Only for items whose opening stock is not yet verified; verified items need an ADJUSTMENT with sanction.
CREATE FUNCTION post_count_correction_with_issue(
  p_org int, p_store int, p_item int, p_counted numeric, p_date date,
  p_requested_by text, p_supervisor text, p_issue_uom text, p_issue_qty numeric, p_issue_approved_by text)
RETURNS text[] LANGUAGE plpgsql SECURITY DEFINER SET search_path = ebhoral, pg_temp AS $$
DECLARE v_verified boolean; v_sys numeric; v_diff numeric; v_cc text; v_iv text; v_base text;
BEGIN
  IF p_supervisor IS NULL OR p_supervisor = p_requested_by THEN
    RAISE EXCEPTION 'count correction must be approved by a different supervisor';
  END IF;
  SELECT opening_verified INTO v_verified FROM stock_position_status WHERE store_id = p_store AND item_id = p_item;
  IF coalesce(v_verified, false) THEN
    RAISE EXCEPTION 'opening stock of this item is verified: use an ADJUSTMENT against a sanction reference';
  END IF;
  PERFORM 1 FROM chain_head WHERE org_id = p_org FOR UPDATE;
  SELECT qty_base INTO v_sys FROM stock_balance WHERE store_id = p_store AND item_id = p_item;
  v_sys := coalesce(v_sys, 0);
  v_diff := p_counted - v_sys;
  SELECT base_uom INTO v_base FROM item WHERE item_id = p_item;
  IF v_diff <> 0 THEN
    v_cc := post_txn(p_org, 'CC', 'COUNT_CORRECTION', p_date, p_requested_by, p_supervisor,
                     'shelf count before issue', NULL, NULL, NULL,
                     jsonb_build_array(jsonb_build_object('store_id', p_store, 'item_id', p_item,
                                                          'uom', v_base, 'qty', v_diff, 'factor', 1)));
  END IF;
  v_iv := post_txn(p_org, 'IV', 'ISSUE', p_date, p_requested_by, p_issue_approved_by, NULL, NULL, NULL, NULL,
                   jsonb_build_array(jsonb_build_object('store_id', p_store, 'item_id', p_item,
                                                        'uom', p_issue_uom, 'qty', -p_issue_qty)));
  INSERT INTO count_correction (org_id, store_id, item_id, system_qty, counted_qty, requested_by,
                                approved_by, cc_voucher_no, issue_voucher_no)
  VALUES (p_org, p_store, p_item, v_sys, p_counted, p_requested_by, p_supervisor, v_cc, v_iv);
  RETURN ARRAY[v_cc, v_iv];
END $$;

-- Daily anchor: the chain head and voucher ranges at the moment the evening digest is produced.
CREATE FUNCTION make_anchor(p_org int, p_date date) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ebhoral, pg_temp AS $$
DECLARE v_seq bigint; v_hash text; v_ranges jsonb; v_gaps text;
BEGIN
  SELECT last_seq, last_hash INTO v_seq, v_hash FROM chain_head WHERE org_id = p_org FOR UPDATE;
  SELECT coalesce(jsonb_object_agg(series || '/' || fin_year, last_no ORDER BY series, fin_year), '{}'::jsonb)
    INTO v_ranges FROM voucher_series WHERE org_id = p_org AND last_no > 0;
  SELECT CASE WHEN count(*) = 0 THEN 'OK' ELSE 'GAPS IN ' || string_agg(k, ', ') END INTO v_gaps FROM (
    SELECT s.series || '/' || s.fin_year AS k
      FROM voucher_series s
      LEFT JOIN stock_txn t ON t.org_id = s.org_id AND t.series = s.series AND t.fin_year = s.fin_year
     WHERE s.org_id = p_org AND s.last_no > 0
     GROUP BY s.series, s.fin_year, s.last_no
    HAVING count(t.txn_id) <> s.last_no) g;
  INSERT INTO ledger_anchor (org_id, anchor_date, last_seq, last_hash, voucher_ranges, gap_check)
  VALUES (p_org, p_date, v_seq, v_hash, v_ranges, v_gaps);
  RETURN jsonb_build_object('org_id', p_org, 'anchor_date', p_date, 'last_seq', v_seq,
                            'last_hash', v_hash, 'voucher_ranges', v_ranges, 'gap_check', v_gaps);
END $$;

-- Second safeguard: append-only tables reject UPDATE, DELETE and TRUNCATE even for their owner.
CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on %.% is not allowed: this table is append-only', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END $$;

CREATE TRIGGER stock_txn_append_only      BEFORE UPDATE OR DELETE ON stock_txn          FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER ledger_entry_append_only   BEFORE UPDATE OR DELETE ON stock_ledger_entry FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER anchor_append_only         BEFORE UPDATE OR DELETE ON ledger_anchor      FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER stock_txn_no_truncate      BEFORE TRUNCATE ON stock_txn          FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER ledger_entry_no_truncate   BEFORE TRUNCATE ON stock_ledger_entry FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER anchor_no_truncate         BEFORE TRUNCATE ON ledger_anchor      FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
