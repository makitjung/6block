# 순환 참조와 무한루프 테스트. subprocess 타임아웃으로 안전하게 감지한다.
import subprocess
import sys

import pytest


def test_lt_tree_order_self_circular():
    """항목이 자기 자신을 상위로 지정할 때, lt_tree_order가 무한루프되지 않는가."""
    code = """
import sys
sys.path.insert(0, '/Users/jinhyugjung/dev/6block')

import tempfile, pathlib, os
TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="test-"))
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TMP_ROOT / "cloud")
for key in ("GCAL_ICAL_URL", "GCAL_WRITE_CALENDAR_ID", "AI_API_KEY"):
    os.environ[key] = ""

import app.config as cfg
cfg.DB_PATH = TMP_ROOT / "blocks.db"
import app.db as db
db.DB_PATH = cfg.DB_PATH

from app.common import lt_tree_order

rows = [{"id": 1, "parent_id": 1, "title": "self-loop", "has_children": 0}]
result = lt_tree_order(rows)
print(f"OK: {len(result)} items")
"""
    try:
        output = subprocess.run(
            [sys.executable, "-c", code],
            timeout=10,
            capture_output=True,
            text=True,
            cwd="/Users/jinhyugjung/dev/6block",
        )
        assert output.returncode == 0, f"stderr: {output.stderr}"
    except subprocess.TimeoutExpired:
        pytest.fail("lt_tree_order가 자기 순환에서 무한루프됨")


def test_lt_tree_order_mutual_circular():
    """2개 항목이 서로를 가리킬 때, lt_tree_order가 무한루프되지 않는가."""
    code = """
import sys
sys.path.insert(0, '/Users/jinhyugjung/dev/6block')

import tempfile, pathlib, os
TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="test-"))
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TMP_ROOT / "cloud")
for key in ("GCAL_ICAL_URL", "GCAL_WRITE_CALENDAR_ID", "AI_API_KEY"):
    os.environ[key] = ""

import app.config as cfg
cfg.DB_PATH = TMP_ROOT / "blocks.db"
import app.db as db
db.DB_PATH = cfg.DB_PATH

from app.common import lt_tree_order

rows = [
    {"id": 1, "parent_id": 2, "title": "a", "has_children": 1},
    {"id": 2, "parent_id": 1, "title": "b", "has_children": 1},
]
result = lt_tree_order(rows)
print(f"OK: {len(result)} items")
"""
    try:
        output = subprocess.run(
            [sys.executable, "-c", code],
            timeout=10,
            capture_output=True,
            text=True,
            cwd="/Users/jinhyugjung/dev/6block",
        )
        assert output.returncode == 0, f"stderr: {output.stderr}"
    except subprocess.TimeoutExpired:
        pytest.fail("lt_tree_order가 상호 순환에서 무한루프됨")


def test_lt_tree_order_chain_circular():
    """3개 이상이 순환할 때 (A→B→C→A), lt_tree_order가 무한루프되지 않는가."""
    code = """
import sys
sys.path.insert(0, '/Users/jinhyugjung/dev/6block')

import tempfile, pathlib, os
TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="test-"))
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TMP_ROOT / "cloud")
for key in ("GCAL_ICAL_URL", "GCAL_WRITE_CALENDAR_ID", "AI_API_KEY"):
    os.environ[key] = ""

import app.config as cfg
cfg.DB_PATH = TMP_ROOT / "blocks.db"
import app.db as db
db.DB_PATH = cfg.DB_PATH

from app.common import lt_tree_order

rows = [
    {"id": 1, "parent_id": 2, "title": "a", "has_children": 1},
    {"id": 2, "parent_id": 3, "title": "b", "has_children": 1},
    {"id": 3, "parent_id": 1, "title": "c", "has_children": 1},
]
result = lt_tree_order(rows)
print(f"OK: {len(result)} items")
"""
    try:
        output = subprocess.run(
            [sys.executable, "-c", code],
            timeout=10,
            capture_output=True,
            text=True,
            cwd="/Users/jinhyugjung/dev/6block",
        )
        assert output.returncode == 0, f"stderr: {output.stderr}"
    except subprocess.TimeoutExpired:
        pytest.fail("lt_tree_order가 3-사이클 순환에서 무한루프됨")


def test_plan_lt_root_circular():
    """plan._lt_root가 순환에서 무한루프되지 않는가."""
    code = """
import sys
sys.path.insert(0, '/Users/jinhyugjung/dev/6block')

import tempfile, pathlib, os
from datetime import date
TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="test-"))
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TMP_ROOT / "cloud")
for key in ("GCAL_ICAL_URL", "GCAL_WRITE_CALENDAR_ID", "AI_API_KEY"):
    os.environ[key] = ""

import app.config as cfg
cfg.DB_PATH = TMP_ROOT / "blocks.db"
import app.db as db
db.DB_PATH = cfg.DB_PATH
db.init_db()

from app.routes.plan import _lt_root
now = date.today().isoformat()

with db.get_conn() as conn:
    area_id = conn.execute("INSERT INTO lt_area (name, display_order) VALUES (?, ?)",
                           ("test", 0)).lastrowid
    item1_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "item1", now, now, 0, now)
    ).lastrowid
    item2_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, item1_id, "item2", now, now, 0, now)
    ).lastrowid
    # 순환 생성: item1의 parent를 item2로
    conn.execute("UPDATE lt_item SET parent_id = ? WHERE id = ?", (item2_id, item1_id))
    result = _lt_root(conn, item1_id)
    print(f"Root: {result}")
"""
    try:
        output = subprocess.run(
            [sys.executable, "-c", code],
            timeout=10,
            capture_output=True,
            text=True,
            cwd="/Users/jinhyugjung/dev/6block",
        )
        assert output.returncode == 0, f"stderr: {output.stderr}"
    except subprocess.TimeoutExpired:
        pytest.fail("_lt_root가 순환에서 무한루프됨")


def test_http_reparent_self_rejected(client, fresh_db, conn):
    """POST /plan/item/reparent가 자기를 상위로 지정하는 것을 거절하는가."""
    from datetime import date
    now = date.today().isoformat()

    area_id = conn.execute("INSERT INTO lt_area (name, display_order) VALUES (?, ?)",
                           ("test", 0)).lastrowid
    item_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "item1", now, now, 0, now)
    ).lastrowid
    conn.commit()

    response = client.post(
        "/plan/item/reparent",
        data={"id": item_id, "parent_id": item_id},
    )

    assert response.status_code == 400
    body = response.json()
    assert not body.get("ok", False)
    assert "자기" in body.get("error", "")


def test_http_reparent_own_child_rejected(client, fresh_db, conn):
    """POST /plan/item/reparent가 자신의 하위 항목을 상위로 지정하는 것을 거절하는가."""
    from datetime import date
    now = date.today().isoformat()

    area_id = conn.execute("INSERT INTO lt_area (name, display_order) VALUES (?, ?)",
                           ("test", 0)).lastrowid
    parent_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "parent", now, now, 0, now)
    ).lastrowid
    child_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, parent_id, "child", now, now, 0, now)
    ).lastrowid
    conn.commit()

    response = client.post(
        "/plan/item/reparent",
        data={"id": parent_id, "parent_id": child_id},
    )

    assert response.status_code == 400
    body = response.json()
    assert not body.get("ok", False)


def test_http_reparent_mutual_blocks(client, fresh_db, conn):
    """첫 번째 move는 통과, 두 번째(순환 만드는) move는 거절되는가."""
    from datetime import date
    now = date.today().isoformat()

    area_id = conn.execute("INSERT INTO lt_area (name, display_order) VALUES (?, ?)",
                           ("test", 0)).lastrowid
    a_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "A", now, now, 0, now)
    ).lastrowid
    b_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "B", now, now, 0, now)
    ).lastrowid
    conn.commit()

    # A를 B 아래로 (통과)
    response1 = client.post(
        "/plan/item/reparent",
        data={"id": a_id, "parent_id": b_id},
    )
    assert response1.status_code == 200

    # B를 A 아래로 (거절 - 순환 방지)
    response2 = client.post(
        "/plan/item/reparent",
        data={"id": b_id, "parent_id": a_id},
    )
    assert response2.status_code == 400


def test_http_reparent_grandchild_rejected(client, fresh_db, conn):
    """손주(A>B>C)에서 A를 C의 하위로 넣을 때 거절하는가."""
    from datetime import date
    now = date.today().isoformat()

    area_id = conn.execute("INSERT INTO lt_area (name, display_order) VALUES (?, ?)",
                           ("test", 0)).lastrowid
    a_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, None, "A", now, now, 0, now)
    ).lastrowid
    b_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, a_id, "B", now, now, 0, now)
    ).lastrowid
    c_id = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (area_id, b_id, "C", now, now, 0, now)
    ).lastrowid
    conn.commit()

    response = client.post(
        "/plan/item/reparent",
        data={"id": a_id, "parent_id": c_id},
    )

    assert response.status_code == 400
