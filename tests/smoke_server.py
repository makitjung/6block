# 스모크 테스트 전용 서버. 임시 DB·임시 포트로 띄워 운영 DB(~/6block-data)와 8000 포트를 건드리지 않는다.
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DIR = pathlib.Path(os.environ["SIXBLOCK_TEST_DIR"])
TEST_DIR.mkdir(parents=True, exist_ok=True)

import app.config as cfg  # noqa: E402

cfg.DB_PATH = TEST_DIR / "blocks.db"
cfg.BACKUP_DIR = TEST_DIR / "backups"
cfg.CLOUD_BACKUP_DIR = TEST_DIR / "cloud"
cfg.GCAL_CALENDARS = []

import app.db as db  # noqa: E402

db.DB_PATH = cfg.DB_PATH

from app.main import app  # noqa: E402
import app.integrations.gcal_write as gcal_write  # noqa: E402
import app.integrations.things as things  # noqa: E402
import app.routes.settings as settings_routes  # noqa: E402

# .env 편집기는 임시 폴더의 가짜 .env 를 보게 한다(실제 프로젝트 .env 를 절대 건드리지 않는다).
TEST_ENV = TEST_DIR / ".env"
if not TEST_ENV.exists():
    TEST_ENV.write_text(
        "# 테스트용\nAI_API_KEY=sk-test-secret\nAI_MODEL=test-model\nEMPTY=\n",
        encoding="utf-8",
    )
settings_routes._env_file_path = lambda: TEST_ENV

# 외부 연동은 테스트에서 끈다(구글 API 호출·AppleScript 권한 창을 띄우지 않기 위함).
things.enabled = lambda: False
things.today_tasks = lambda *a, **k: []
gcal_write.enabled = lambda: False
gcal_write.events_enabled = lambda: False
gcal_write.achieve_enabled = lambda: False
gcal_write.service_account_email = lambda: ""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("SIXBLOCK_TEST_PORT", "8011")),
                log_level="warning")
