-- e-Bhoral ledger core: schema.
-- Run as the owning role (ebhoral_owner). The application role never owns or alters these tables.
CREATE SCHEMA IF NOT EXISTS ebhoral;
SET search_path = ebhoral;

CREATE TABLE organisation (
  org_id  int  PRIMARY KEY,
  code    text NOT NULL UNIQUE,
  name    text NOT NULL
);

CREATE TABLE store (
  store_id int  PRIMARY KEY,
  org_id   int  NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  code     text NOT NULL,
  name     text NOT NULL,
  UNIQUE (org_id, code)
);

CREATE TABLE item (
  item_id  int  PRIMARY KEY,
  org_id   int  NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  code     text NOT NULL,
  name     text NOT NULL,
  base_uom text NOT NULL,
  UNIQUE (org_id, code)
);

-- Unit conversions, e.g. 1 ream = 500 sheets. The factor in force is copied onto every ledger line.
CREATE TABLE item_uom (
  item_id        int     NOT NULL REFERENCES item ON DELETE RESTRICT,
  uom            text    NOT NULL,
  factor_to_base numeric NOT NULL CHECK (factor_to_base > 0),
  PRIMARY KEY (item_id, uom)
);

-- Whether the opening stock of an item in a store has been verified by a physical count.
CREATE TABLE stock_position_status (
  org_id           int     NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  store_id         int     NOT NULL REFERENCES store ON DELETE RESTRICT,
  item_id          int     NOT NULL REFERENCES item ON DELETE RESTRICT,
  opening_verified boolean NOT NULL DEFAULT false,
  PRIMARY KEY (store_id, item_id)
);

-- Gapless voucher counter: one row per series per financial year, locked inside the posting transaction.
-- A database SEQUENCE is deliberately not used: sequences can skip numbers when a transaction fails.
CREATE TABLE voucher_series (
  org_id   int  NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  series   text NOT NULL CHECK (series IN ('OPN','GRN','IV','TRF','RET','ADJ','CC','REV')),
  fin_year text NOT NULL,
  last_no  int  NOT NULL DEFAULT 0 CHECK (last_no >= 0),
  PRIMARY KEY (org_id, series, fin_year)
);

-- Head of the hash chain for each organisation. Locking this row serialises postings per organisation.
CREATE TABLE chain_head (
  org_id    int    PRIMARY KEY REFERENCES organisation ON DELETE RESTRICT,
  last_seq  bigint NOT NULL DEFAULT 0,
  last_hash text   NOT NULL
);

-- Voucher header for every stock movement. APPEND-ONLY.
CREATE TABLE stock_txn (
  txn_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id          int    NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  voucher_no      text   NOT NULL UNIQUE,
  series          text   NOT NULL,
  fin_year        text   NOT NULL,
  txn_type        text   NOT NULL CHECK (txn_type IN
                    ('OPENING','RECEIPT','ISSUE','TRANSFER','RETURN','ADJUSTMENT','COUNT_CORRECTION','REVERSAL')),
  txn_date        date   NOT NULL,
  posted_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
  posted_by       text   NOT NULL,
  approved_by     text,
  reason          text,
  sanction_ref    text,
  reverses_txn_id bigint UNIQUE REFERENCES stock_txn ON DELETE RESTRICT,   -- each voucher can be reversed once
  client_uuid     uuid   UNIQUE,                                           -- idempotent submission
  CONSTRAINT approver_differs_from_poster CHECK (approved_by IS NULL OR approved_by <> posted_by),
  CONSTRAINT adjustment_needs_reason_and_sanction CHECK (
    txn_type NOT IN ('ADJUSTMENT','REVERSAL')
    OR (coalesce(reason, '') <> '' AND coalesce(sanction_ref, '') <> '')),
  CONSTRAINT reversal_links_original CHECK ((txn_type = 'REVERSAL') = (reverses_txn_id IS NOT NULL))
);

-- Ledger lines. APPEND-ONLY and hash-chained in posting order.
CREATE TABLE stock_ledger_entry (
  org_id          int     NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  seq             bigint  NOT NULL,
  txn_id          bigint  NOT NULL REFERENCES stock_txn ON DELETE RESTRICT,
  voucher_no      text    NOT NULL,
  store_id        int     NOT NULL REFERENCES store ON DELETE RESTRICT,
  item_id         int     NOT NULL REFERENCES item ON DELETE RESTRICT,
  qty_base        numeric NOT NULL CHECK (qty_base <> 0),
  uom             text    NOT NULL,
  qty_entered     numeric NOT NULL,
  factor_snapshot numeric NOT NULL CHECK (factor_snapshot > 0),
  prev_hash       text    NOT NULL,
  entry_hash      text    NOT NULL,
  PRIMARY KEY (org_id, seq)
);

-- Derived balance, written only by the posting function. Can never go negative.
CREATE TABLE stock_balance (
  org_id   int     NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  store_id int     NOT NULL REFERENCES store ON DELETE RESTRICT,
  item_id  int     NOT NULL REFERENCES item ON DELETE RESTRICT,
  qty_base numeric NOT NULL CONSTRAINT no_negative_stock CHECK (qty_base >= 0),
  PRIMARY KEY (store_id, item_id)
);

-- Daily integrity anchor; its content is what the evening digest emails out. APPEND-ONLY.
CREATE TABLE ledger_anchor (
  org_id         int    NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  anchor_date    date   NOT NULL,
  last_seq       bigint NOT NULL,
  last_hash      text   NOT NULL,
  voucher_ranges jsonb  NOT NULL,
  gap_check      text   NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (org_id, anchor_date)
);

-- Supervisor-approved count correction for stock whose opening balance is not yet verified.
CREATE TABLE count_correction (
  cc_id            bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id           int     NOT NULL REFERENCES organisation ON DELETE RESTRICT,
  store_id         int     NOT NULL REFERENCES store ON DELETE RESTRICT,
  item_id          int     NOT NULL REFERENCES item ON DELETE RESTRICT,
  system_qty       numeric NOT NULL,
  counted_qty      numeric NOT NULL CHECK (counted_qty >= 0),
  requested_by     text    NOT NULL,
  approved_by      text    NOT NULL,
  cc_voucher_no    text,
  issue_voucher_no text    NOT NULL,
  count_task_open  boolean NOT NULL DEFAULT true,
  CONSTRAINT supervisor_differs_from_requester CHECK (approved_by <> requested_by)
);
