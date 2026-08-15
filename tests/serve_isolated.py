# 브라우저로 눈으로 확인할 때 쓰는 격리 서버. 임시 DB·임시 .env·외부연동 끔으로 띄운다.
# 운영 DB(~/6block-data)와 8000 포트는 절대 건드리지 않는다.
# 실행 · .venv/bin/python tests/serve_isolated.py   (포트는 SIXBLOCK_TEST_PORT, 기본 8024)
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DIR = pathlib.Path(
    os.environ.get("SIXBLOCK_TEST_DIR") or tempfile.mkdtemp(prefix="6block-preview-")
)
TEST_DIR.mkdir(parents=True, exist_ok=True)
os.environ["SIXBLOCK_TEST_DIR"] = str(TEST_DIR)

# 외부 연동 환경값을 비운 뒤에 config 를 읽게 한다(load_dotenv 는 기존 환경값을 덮지 않는다).
for _key in ("GCAL_ICAL_URL", "GCAL_ICAL_URL_2", "GCAL_WRITE_CALENDAR_ID",
             "GCAL_WRITE_EVENTS_CALENDAR_ID", "GCAL_WRITE_ACHIEVE_CALENDAR_ID",
             "GCAL_SA_KEYFILE", "AI_API_KEY", "AI_BASE_URL", "AI_MODEL"):
    os.environ[_key] = ""
os.environ["SIXBLOCK_CLOUD_DIR"] = str(TEST_DIR / "cloud")

import app.config as cfg  # noqa: E402

cfg.DB_PATH = TEST_DIR / "blocks.db"
cfg.BACKUP_DIR = TEST_DIR / "backups"
cfg.CLOUD_BACKUP_DIR = TEST_DIR / "cloud" / "backups"
cfg.GCAL_CALENDARS = []

import app.db as db  # noqa: E402

db.DB_PATH = cfg.DB_PATH

import app.integrations.ai as ai  # noqa: E402
import app.integrations.gcal_write as gcal_write  # noqa: E402
import app.integrations.things as things  # noqa: E402
import app.routes.settings as settings_routes  # noqa: E402
from app.main import app  # noqa: E402

settings_routes.BACKUP_DIR = cfg.BACKUP_DIR
settings_routes.CLOUD_BACKUP_DIR = cfg.CLOUD_BACKUP_DIR

TEST_ENV = TEST_DIR / ".env"
if not TEST_ENV.exists():
    TEST_ENV.write_text("# 미리보기용\nAI_API_KEY=sk-preview-secret\n", encoding="utf-8")
settings_routes._env_file_path = lambda: TEST_ENV

things.enabled = lambda: False
things.today_tasks = lambda *a, **k: []
gcal_write.enabled = lambda: False
gcal_write.write_enabled = lambda which: False
gcal_write.service_account_email = lambda: ""
ai.enabled = lambda: False
ai.complete = lambda *a, **k: None

if __name__ == "__main__":
    import uvicorn

    print(f"격리 서버 데이터 폴더: {TEST_DIR}")
    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("SIXBLOCK_TEST_PORT", "8024")),
                log_level="info")
