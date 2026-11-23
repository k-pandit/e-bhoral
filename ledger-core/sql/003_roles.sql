-- e-Bhoral ledger core: privileges for the application role.
-- The application can READ everything and maintain the catalogue, but it can only CHANGE stock
-- through the posting functions. It has no UPDATE, DELETE or TRUNCATE on any ledger table.
SET search_path = ebhoral;

REVOKE ALL ON ALL TABLES IN SCHEMA ebhoral FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ebhoral FROM PUBLIC;

GRANT USAGE  ON SCHEMA ebhoral TO ebhoral_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ebhoral TO ebhoral_app;
GRANT INSERT, UPDATE ON store, item, item_uom, stock_position_status TO ebhoral_app;   -- catalogue maintenance
GRANT UPDATE (count_task_open) ON count_correction TO ebhoral_app;                    -- closing a count task

GRANT EXECUTE ON FUNCTION
  fin_year(date),
  init_organisation(int, text, text),
  post_txn(int, text, text, date, text, text, text, text, bigint, uuid, jsonb),
  post_reversal(int, text, date, text, text, text, text),
  post_count_correction_with_issue(int, int, int, numeric, date, text, text, text, numeric, text),
  make_anchor(int, date)
TO ebhoral_app;
