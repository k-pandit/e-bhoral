"""Database helpers: start an embedded PostgreSQL, create a database, install the ledger core.

pgserver ships real PostgreSQL binaries inside a Python wheel, so the tests run against PostgreSQL 16
on Windows, Linux or macOS without Docker or a separate installation.
"""
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pg8000.native

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_SERVER = None


def server():
    """Start (once) an embedded PostgreSQL server and return (host, port)."""
    global _SERVER
    if _SERVER is None:
        import pgserver
        data_dir = os.environ.get("EBHORAL_PGDATA") or os.path.join(tempfile.gettempdir(), "ebhoral-ledger-core-pgdata")
        _SERVER = pgserver.get_server(data_dir, cleanup_mode=None)
    u = urlparse(_SERVER.get_uri())
    return u.hostname, u.port


def connect(database="postgres", user="postgres"):
    host, port = server()
    return pg8000.native.Connection(user=user, host=host, port=port, database=database)


def _run_sql_file(conn, path):
    sql = Path(path).read_text(encoding="utf-8")
    # Split on statement terminators outside dollar-quoted function bodies.
    statements, buf, in_dollar = [], [], False
    for line in sql.splitlines():
        if line.strip().startswith("--") and not in_dollar:
            continue
        buf.append(line)
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        if not in_dollar and line.rstrip().endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if "".join(buf).strip():
        statements.append("\n".join(buf))
    for s in statements:
        conn.run(s)


def ensure_roles(admin):
    for role, login in (("ebhoral_owner", False), ("ebhoral_app", True)):
        exists = admin.run("SELECT 1 FROM pg_roles WHERE rolname = :r", r=role)
        if not exists:
            admin.run(f"CREATE ROLE {role} {'LOGIN' if login else 'NOLOGIN'}")


def create_database(name, template=None):
    """Create a fresh database. With template=None the ledger core is installed into it."""
    admin = connect()
    try:
        ensure_roles(admin)
        admin.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if template:
            admin.run(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
            return
        admin.run(f'CREATE DATABASE "{name}"')
        admin.run(f'GRANT CREATE, CONNECT ON DATABASE "{name}" TO ebhoral_owner, ebhoral_app')
    finally:
        admin.close()
    owner = connect(name)
    try:
        owner.run("SET ROLE ebhoral_owner")
        for f in ("001_schema.sql", "002_ledger.sql", "003_roles.sql"):
            _run_sql_file(owner, SQL_DIR / f)
    finally:
        owner.close()
