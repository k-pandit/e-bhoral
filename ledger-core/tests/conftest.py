import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ebhoral_core import db, posting  # noqa: E402

ARTIFACTS = ROOT / "reports" / "artifacts"
TEMPLATE = "ebhoral_tpl"
ORG = 1
MAIN, PRESS = 1, 2


def save_artifact(name, data):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


@pytest.fixture(scope="session")
def template():
    db.create_database(TEMPLATE)
    return TEMPLATE


@pytest.fixture
def dbname(template):
    name = "ebhoral_t_" + uuid.uuid4().hex[:8]
    db.create_database(name, template=template)
    yield name
    admin = db.connect()
    admin.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pytest.fixture
def app(dbname):
    """Connection as the application role (what the e-Bhoral web app would use)."""
    c = db.connect(dbname, user="ebhoral_app")
    yield c
    c.close()


@pytest.fixture
def superuser(dbname):
    """Connection as a database superuser: used only to simulate an insider attack."""
    c = db.connect(dbname)
    yield c
    c.close()


@pytest.fixture
def org(app):
    """Synthetic Directorate with two stores and three items (SYNTHETIC DATA)."""
    posting.init_org(app, ORG, "DPNS-SYNTHETIC", "Directorate of Printing & Stationery (synthetic)")
    posting.add_store(app, MAIN, ORG, "MAIN", "Main store (synthetic)")
    posting.add_store(app, PRESS, ORG, "PRESS", "Press store (synthetic)")
    posting.add_item(app, 1, ORG, "PAP-A4-70", "A4 copier paper", "sheet", {"ream": 500}, verified=True, stores=(MAIN, PRESS))
    posting.add_item(app, 2, ORG, "STN-FC", "File cover", "piece", {"bundle": 50}, verified=True, stores=(MAIN, PRESS))
    posting.add_item(app, 3, ORG, "FRM-SB", "Service book", "piece", {}, verified=False, stores=(MAIN, PRESS))
    return ORG
