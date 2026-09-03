# 테스트 격리 장치. 실제 DB(~/6block-data)·실제 .env·운영 서버(8000)를 절대 건드리지 않게 막는다.
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# 1. app.config 를 import 하기 전에 외부 연동 환경값을 비운다.
#    python-dotenv 의 load_dotenv 는 override=False 라, 여기서 먼저 넣어 둔 빈 값이
#    실제 .env 의 값을 이긴다. 이걸 안 하면 테스트가 진짜 구글 캘린더를 부른다.
# ---------------------------------------------------------------------------
TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="6block-test-"))
REAL_DATA_DIR = pathlib.Path.home() / "6block-data"
REAL_ENV_FILE = ROOT / ".env"

for _key in (
    "GCAL_ICAL_URL", "GCAL_ICAL_URL_2",
    "GCAL_WRITE_CALENDAR_ID", "GCAL_WRITE_EVENTS_CALENDAR_ID",
    "GCAL_WRITE_ACHIEVE_CALENDAR_ID", "GCAL_SA_KEYFILE",
    "AI_API_KEY", "AI_BASE_URL", "AI_MODEL",
    "SIXBLOCK_ALLOWED_ORIGINS",
):
    os.environ[_key] = ""
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TMP_ROOT / "cloud")

# ---------------------------------------------------------------------------
# 2. 실제 사용자 데이터로 가는 길을 물리적으로 막는다.
#    이 가드가 없으면 픽스처 하나만 잘못 짜도 진짜 기록이 지워진다.
# ---------------------------------------------------------------------------
_real_connect = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    if str(REAL_DATA_DIR) in str(database):
        raise AssertionError(f"테스트가 실제 DB를 열려고 했다: {database}")
    return _real_connect(database, *args, **kwargs)


sqlite3.connect = _guarded_connect

# ---------------------------------------------------------------------------
# 3. 앱 모듈을 불러오고 경로를 임시 폴더로 갈아끼운다.
#    db.py 는 `from app.config import DB_PATH` 로 값을 복사해 갔으므로 양쪽 다 바꿔야 한다.
# ---------------------------------------------------------------------------
import app.config as cfg  # noqa: E402

TEST_DB = TMP_ROOT / "blocks.db"
cfg.DB_PATH = TEST_DB
cfg.BACKUP_DIR = TMP_ROOT / "backups"
cfg.CLOUD_BACKUP_DIR = TMP_ROOT / "cloud" / "backups"
cfg.GCAL_CALENDARS = []

import app.db as db  # noqa: E402

db.DB_PATH = TEST_DB

import app.integrations.ai as ai  # noqa: E402
import app.integrations.gcal as gcal  # noqa: E402
import app.integrations.gcal_write as gcal_write  # noqa: E402
import app.integrations.things as things  # noqa: E402
import app.routes.settings as settings_routes  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

settings_routes.BACKUP_DIR = cfg.BACKUP_DIR
settings_routes.CLOUD_BACKUP_DIR = cfg.CLOUD_BACKUP_DIR

# .env 편집기는 임시 폴더의 가짜 .env 만 보게 한다.
TEST_ENV = TMP_ROOT / ".env"
TEST_ENV.write_text(
    "# 테스트용\nAI_API_KEY=sk-test-secret\nAI_MODEL=test-model\nEMPTY=\n",
    encoding="utf-8",
)
_REAL_ENV_FILE_PATH = settings_routes._env_file_path
settings_routes._env_file_path = lambda: TEST_ENV

# 스텁으로 덮기 전의 진짜 함수. 연동 자체를 시험하는 테스트가 되돌려 쓴다.
REAL = {
    "things.enabled": things.enabled,
    "things.today_tasks": things.today_tasks,
    "gcal.events_for_date": gcal.events_for_date,
    "ai.enabled": ai.enabled,
    "ai.complete": ai.complete,
    "gcal_write.enabled": gcal_write.enabled,
    "gcal_write.write_enabled": gcal_write.write_enabled,
    "gcal_write.service_account_email": gcal_write.service_account_email,
    "settings._env_file_path": _REAL_ENV_FILE_PATH,
}

# 외부 연동은 전부 끈다(구글 호출·AppleScript 권한창·AI 과금이 일어나지 않게).
things.enabled = lambda: False
things.today_tasks = lambda *a, **k: []
gcal_write.enabled = lambda: False
gcal_write.write_enabled = lambda which: False
gcal_write.service_account_email = lambda: ""
gcal.events_for_date = lambda *a, **k: []
ai.enabled = lambda: False
ai.complete = lambda *a, **k: None


def pytest_sessionfinish(session, exitstatus):
    """테스트가 만든 임시 폴더를 지운다."""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def guard_real_data():
    """모든 테스트에서 실제 데이터 경로와 실제 .env 가 그대로인지 확인한다."""
    env_before = REAL_ENV_FILE.read_bytes() if REAL_ENV_FILE.exists() else None
    yield
    assert db.DB_PATH == TEST_DB, "테스트 중 DB 경로가 실제 경로로 되돌아갔다"
    env_after = REAL_ENV_FILE.read_bytes() if REAL_ENV_FILE.exists() else None
    assert env_before == env_after, "테스트가 실제 .env 를 건드렸다"


@pytest.fixture
def fresh_db():
    """빈 DB 를 새로 만들고 초기화한다. 테스트끼리 상태가 새지 않게 한다."""
    for suffix in ("", "-wal", "-shm"):
        pathlib.Path(str(TEST_DB) + suffix).unlink(missing_ok=True)
    db._settings_cache = None
    db._week_times_cache = None     # 주별·일별 세션시간도 캐시된다(안 비우면 앞 테스트 값이 샌다)
    db._day_times_cache = None
    db.init_db()
    yield TEST_DB
    db._settings_cache = None
    db._week_times_cache = None
    db._day_times_cache = None


@pytest.fixture
def conn(fresh_db):
    """초기화된 DB 의 연결 하나."""
    with db.get_conn() as c:
        yield c


@pytest.fixture
def real_integrations():
    """스텁을 잠깐 걷어 내고 진짜 연동 코드를 시험한다(네트워크는 테스트가 직접 가짜로 막는다)."""
    saved = {
        "things.enabled": things.enabled,
        "things.today_tasks": things.today_tasks,
        "gcal.events_for_date": gcal.events_for_date,
        "ai.enabled": ai.enabled,
        "ai.complete": ai.complete,
    }
    things.enabled, things.today_tasks = REAL["things.enabled"], REAL["things.today_tasks"]
    gcal.events_for_date = REAL["gcal.events_for_date"]
    ai.enabled, ai.complete = REAL["ai.enabled"], REAL["ai.complete"]
    yield
    things.enabled, things.today_tasks = saved["things.enabled"], saved["things.today_tasks"]
    gcal.events_for_date = saved["gcal.events_for_date"]
    ai.enabled, ai.complete = saved["ai.enabled"], saved["ai.complete"]


@pytest.fixture
def real_stubbed():
    """스텁으로 덮기 전의 진짜 함수 모음. 스텁은 그대로 두고 그 함수만 직접 부를 때 쓴다."""
    return REAL


@pytest.fixture
def tmp_root():
    """테스트가 쓰는 임시 폴더(백업·클라우드·.env 가 전부 여기 안에 있다)."""
    return TMP_ROOT


@pytest.fixture
def test_env_file():
    """테스트용 가짜 .env 경로."""
    return TEST_ENV


@pytest.fixture
def client(fresh_db):
    """앱 전체를 인프로세스로 띄운 HTTP 클라이언트. Origin 헤더가 없어 CSRF 가드는 통과한다."""
    from starlette.testclient import TestClient

    with TestClient(fastapi_app) as c:
        yield c
