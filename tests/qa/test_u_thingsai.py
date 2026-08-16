# Things3 및 AI 연동 모듈 QA. subprocess·HTTP를 mock으로 막고 엣지 케이스를 검증한다.
import json
import subprocess
import time
import unittest.mock
from datetime import date

import pytest

from app.common import _ai_split
from app.integrations import ai, things
from app.routes import analytics


# ==== THINGS3 TESTS ====

class TestThingsRun:
    """_run 함수의 subprocess 호출과 에러 처리."""

    def test_run_success(self):
        """정상 실행: returncode 0, 출력 반환."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(
                returncode=0, stdout="output text"
            )
            rc, out = things._run("test script")
            assert rc == 0
            assert out == "output text"
            mock_run.assert_called_once()

    def test_run_permission_denied(self):
        """권한 거부: returncode != 0."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(
                returncode=1, stdout=""
            )
            rc, out = things._run("test script")
            assert rc == 1
            assert out == ""

    def test_run_timeout_exception(self):
        """타임아웃: 예외 발생."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("osascript", 8)
            rc, out = things._run("test script")
            assert rc is None
            assert out == ""

    def test_run_generic_exception(self):
        """일반 예외: returncode None."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected error")
            rc, out = things._run("test script")
            assert rc is None
            assert out == ""


class TestThingsTodayNames:
    """_today_names 파싱 로직."""

    def test_today_names_simple(self):
        """정상 파싱: 이름<TAB>태그<TAB>id."""
        output = "Task One\t\t12345\nTask Two\ttag1,tag2\t67890\n"
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, output)
            result = things._today_names()
            assert result is not None
            assert len(result) == 2
            assert result[0]["name"] == "Task One"
            assert result[0]["tags"] == []
            assert result[0]["id"] == "12345"
            assert result[1]["name"] == "Task Two"
            assert result[1]["tags"] == ["tag1", "tag2"]

    def test_today_names_tab_in_title(self):
        """제목에 탭이 들어 있을 때: rsplit은 뒤에서부터 2개만 자른다."""
        # "Task\tWith\tTab\tinner-tag\tid"로 들어오면
        # rsplit("\t", 2)는 ["Task\tWith\tTab", "inner-tag", "id"]
        output = "Task\tWith\tTab\tinner-tag\t12345\n"
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, output)
            result = things._today_names()
            assert result is not None
            assert len(result) == 1
            assert result[0]["name"] == "Task\tWith\tTab"
            assert result[0]["tags"] == ["inner-tag"]
            assert result[0]["id"] == "12345"

    def test_today_names_newline_in_title(self):
        """제목에 개행이 들어 있을 때(Things에서 붙여넣기): 스트림 파싱 확인."""
        # AppleScript의 linefeed 구분자이므로 여기서는 "\n"으로 구분된다.
        # 제목 자체에 "\n"이 있으면 여러 줄로 나타난다.
        output = "Task\nMultiline\t\t123\n"
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, output)
            result = things._today_names()
            # 파싱은 ln.strip()으로 빈 줄을 버리고 각 비어있지 않은 줄을 처리한다
            # "Task" 줄: "Task"는 파싱되고, "Multiline\t\t123"도 파싱된다
            assert result is not None
            # "Task"는 탭이 없으므로 parts[0]만 유효
            assert len(result) == 2
            assert result[0]["name"] == "Task"

    def test_today_names_unicode(self):
        """유니코드 문자(한글·이모지)가 제목에 있을 때."""
        output = "한글 제목\t한글태그\t가나다\nEmoji 😀🎉\ttag\t12345\n"
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, output)
            result = things._today_names()
            assert result is not None
            assert len(result) == 2
            assert result[0]["name"] == "한글 제목"
            assert result[0]["tags"] == ["한글태그"]
            assert result[1]["name"] == "Emoji 😀🎉"

    def test_today_names_quotes_in_title(self):
        """제목에 따옴표가 들어 있을 때."""
        output = 'Task with "quotes"\t\t123\n'
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, output)
            result = things._today_names()
            assert result is not None
            assert result[0]["name"] == 'Task with "quotes"'

    def test_today_names_permission_denied(self):
        """권한 거부: returncode != 0."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (1, "")
            result = things._today_names()
            assert result is None

    def test_today_names_empty_output(self):
        """빈 출력."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, "")
            result = things._today_names()
            assert result == []


class TestThingsFetchIntoCache:
    """캐시 관리 로직."""

    def test_fetch_into_cache_success(self, fresh_db):
        """성공: 캐시에 저장."""
        things._cache = {"at": 0.0, "items": None}
        with unittest.mock.patch.object(things, "_today_names") as mock:
            mock.return_value = [{"name": "Task", "tags": [], "id": "1"}]
            result = things._fetch_into_cache()
            assert result is not None
            assert things._cache["items"] is not None
            assert things._cache["at"] > 0

    def test_fetch_into_cache_failure_keeps_old_cache(self, fresh_db):
        """실패: 이전 캐시 유지."""
        old_time = time.time() - 100
        things._cache = {
            "at": old_time,
            "items": [{"name": "Old Task", "tags": [], "id": "1"}]
        }
        with unittest.mock.patch.object(things, "_today_names") as mock:
            mock.return_value = None  # 실패
            result = things._fetch_into_cache()
            assert result is None
            # 캐시는 유지
            assert things._cache["items"] == [{"name": "Old Task", "tags": [], "id": "1"}]
            assert things._cache["at"] == old_time


class TestThingsTodayTasks:
    """today_tasks 함수의 오늘 날짜 필터링과 캐시 만료."""

    def test_today_tasks_not_today(self, fresh_db, real_integrations):
        """다른 날짜 요청: 빈 목록."""
        result = things.today_tasks(date(2020, 1, 1))
        assert result == []

    def test_today_tasks_permission_denied(self, fresh_db, real_integrations):
        """권한 거부 후 빈 캐시: 빈 목록."""
        things._cache = {"at": 0.0, "items": None}
        with unittest.mock.patch.object(things, "_today_names") as mock:
            mock.return_value = None  # 권한 거부
            result = things.today_tasks(date.today())
            assert result == []

    def test_today_tasks_format_conversion(self, fresh_db, real_integrations):
        """_today_names -> today_tasks 형식 변환."""
        things._cache = {"at": 0.0, "items": None}
        task_data = [{"name": "Test Task", "tags": ["urgent"], "id": "abc123"}]
        with unittest.mock.patch.object(things, "_today_names") as mock:
            mock.return_value = task_data
            result = things.today_tasks(date.today())
            assert len(result) == 1
            assert result[0]["title"] == "Test Task"
            assert result[0]["tags"] == ["urgent"]
            assert result[0]["id"] == "abc123"
            assert result[0]["time"] is None
            assert result[0]["deadline"] is None


class TestThingsStatus:
    """status 함수의 헬스체크."""

    def test_status_ok(self):
        """정상: ok=True, today 개수."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, "5")
            result = things.status()
            assert result["ok"] is True
            assert result["today"] == 5

    def test_status_permission_denied(self):
        """권한 거부."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (1, "")
            result = things.status()
            assert result["ok"] is False
            assert result["reason"] == "automation not permitted"
            assert result["today"] is None

    def test_status_timeout(self):
        """타임아웃."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (None, "")
            result = things.status()
            assert result["ok"] is False
            assert result["reason"] == "osascript timeout/error"

    def test_status_invalid_output(self):
        """출력이 숫자가 아닐 때."""
        with unittest.mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, "not a number")
            result = things.status()
            assert result["ok"] is True
            assert result["today"] is None


class TestThingsAddTodo:
    """add_todo 함수와 따옴표 이스케이프."""

    def test_add_todo_simple(self, real_integrations):
        """간단한 제목."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(returncode=0)
            result = things.add_todo("Simple task")
            assert result is True
            # 호출 확인
            call_args = mock_run.call_args
            assert call_args is not None
            assert call_args[0][0][0] == "osascript"
            assert "Simple task" in call_args[0][0]

    def test_add_todo_with_double_quotes(self, real_integrations):
        """따옴표가 포함된 제목: AppleScript 주입 방지."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(returncode=0)
            title = 'Task with "quotes"'
            result = things.add_todo(title)
            assert result is True
            # 호출 확인
            call_args = mock_run.call_args
            assert call_args is not None
            # title이 argv로 전달되므로, subprocess.run의 인자를 확인
            # argv[1]이 title이어야 한다 (osascript -e script title)
            assert title in call_args[0][0]

    def test_add_todo_with_newline(self, real_integrations):
        """개행이 포함된 제목."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(returncode=0)
            title = "Task\nwith\nnewlines"
            result = things.add_todo(title)
            assert result is True

    def test_add_todo_with_unicode(self, real_integrations):
        """한글·이모지."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(returncode=0)
            title = "한글 제목 😀"
            result = things.add_todo(title)
            assert result is True

    def test_add_todo_empty_title(self, real_integrations):
        """빈 제목."""
        result = things.add_todo("")
        assert result is False
        result = things.add_todo("   ")
        assert result is False

    def test_add_todo_failure(self, real_integrations):
        """실행 실패."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.Mock(returncode=1)
            result = things.add_todo("Task")
            assert result is False

    def test_add_todo_timeout(self, real_integrations):
        """타임아웃."""
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("osascript", 8)
            result = things.add_todo("Task")
            assert result is False


class TestThingsEnabled:
    """enabled 함수: 플랫폼 체크."""

    def test_enabled_darwin(self, real_integrations):
        """macOS."""
        with unittest.mock.patch("sys.platform", "darwin"):
            assert things.enabled() is True

    def test_enabled_linux(self, real_integrations):
        """Linux."""
        with unittest.mock.patch("sys.platform", "linux"):
            assert things.enabled() is False


# ==== AI TESTS ====

class TestAiConfig:
    """_cfg 함수: 설정 로드."""

    def test_cfg_from_env_only(self, fresh_db):
        """키는 .env에서만, 주소·모델은 설정값 또는 .env."""
        key, base, model = ai._cfg()
        # conftest에서 AI_API_KEY, AI_BASE_URL, AI_MODEL 모두 비웠으므로
        # 반환값은 ("", "", "")
        assert key == ""
        assert base == ""
        assert model == ""


class TestAiEnabled:
    """enabled 함수: 키·주소·모델 확인."""

    def test_enabled_all_present(self, fresh_db, real_integrations):
        """모두 있을 때."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            assert ai.enabled() is True

    def test_enabled_missing_key(self, fresh_db, real_integrations):
        """키가 비었을 때."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("", "https://api.example.com", "gpt-4")
            assert ai.enabled() is False

    def test_enabled_missing_base(self, fresh_db, real_integrations):
        """주소가 비었을 때."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("sk-123", "", "gpt-4")
            assert ai.enabled() is False

    def test_enabled_missing_model(self, fresh_db, real_integrations):
        """모델이 비었을 때."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("sk-123", "https://api.example.com", "")
            assert ai.enabled() is False


class TestAiStatus:
    """status 함수: 설정 상태 노출."""

    def test_status_keys_not_exposed(self, fresh_db):
        """API 키 값을 노출하지 않는다."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("sk-test-secret-key", "https://api.example.com", "gpt-4")
            result = ai.status()
            # has_key는 있지만 key/api_key 같은 실제 값은 없어야 함
            assert "has_key" in result
            assert result["has_key"] is True
            assert "key" not in result
            assert "api_key" not in result
            assert "sk-test" not in str(result)
            assert "secret" not in str(result)

    def test_status_base_and_model_exposed(self, fresh_db):
        """주소와 모델은 노출."""
        with unittest.mock.patch.object(ai, "_cfg") as mock:
            mock.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            result = ai.status()
            assert result["base"] == "https://api.example.com"
            assert result["model"] == "gpt-4"
            assert result["enabled"] is True


class TestAiComplete:
    """complete 함수: HTTP 호출과 에러 처리."""

    def test_complete_success(self, fresh_db, real_integrations):
        """정상 응답."""
        response_data = {
            "choices": [{"message": {"content": "AI response"}}]
        }
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps(response_data).encode()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result == "AI response"

    def test_complete_empty_response(self, fresh_db, real_integrations):
        """응답이 빈 문자열."""
        response_data = {
            "choices": [{"message": {"content": ""}}]
        }
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps(response_data).encode()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_whitespace_only_response(self, fresh_db, real_integrations):
        """응답이 공백만."""
        response_data = {
            "choices": [{"message": {"content": "   \n\t  "}}]
        }
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps(response_data).encode()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_broken_json(self, fresh_db, real_integrations):
        """JSON이 깨짐."""
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = b"not json at all"
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_missing_choices(self, fresh_db, real_integrations):
        """choices 필드가 없음."""
        response_data = {"error": "invalid"}
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps(response_data).encode()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_null_choices(self, fresh_db, real_integrations):
        """choices가 null."""
        response_data = {"choices": None}
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps(response_data).encode()
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_http_error(self, fresh_db, real_integrations):
        """HTTP 에러(500 등)."""
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("sk-123", "https://api.example.com", "gpt-4")
            with unittest.mock.patch("urllib.request.urlopen") as mock_urlopen:
                import urllib.error
                mock_urlopen.side_effect = urllib.error.HTTPError(
                    "https://api.example.com", 500, "Internal Server Error", {}, None
                )
                result = ai.complete("system", "user")
                assert result is None

    def test_complete_not_enabled(self, fresh_db, real_integrations):
        """키/주소/모델이 없을 때."""
        with unittest.mock.patch.object(ai, "_cfg") as mock_cfg:
            mock_cfg.return_value = ("", "", "")
            result = ai.complete("system", "user")
            assert result is None


class TestAiSplit:
    """_ai_split 함수: 부모 계획을 자식별 내용으로 나누기."""

    def test_ai_split_success(self, fresh_db):
        """정상 응답."""
        reply = '["계획1", "계획2", "계획3"]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위 계획", ["1분기", "2분기", "3분기"], "영역", "연간")
            assert result == ["계획1", "계획2", "계획3"]

    def test_ai_split_fewer_items(self, fresh_db):
        """반환 배열이 더 짧을 때: 빈 문자열로 채운다."""
        reply = '["계획1"]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a", "b", "c"], "영역", "상위")
            assert result == ["계획1", "", ""]

    def test_ai_split_more_items(self, fresh_db):
        """반환 배열이 더 길 때: 처음 n개만."""
        reply = '["a", "b", "c", "d", "e"]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["x", "y"], "영역", "상위")
            assert result == ["a", "b"]

    def test_ai_split_empty_reply(self, fresh_db):
        """응답이 비었을 때."""
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = None
            result = _ai_split("상위", ["a"], "영역", "상위")
            assert result is None

    def test_ai_split_not_json(self, fresh_db):
        """JSON이 아닐 때."""
        reply = "not json at all"
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a"], "영역", "상위")
            assert result is None

    def test_ai_split_not_array(self, fresh_db):
        """JSON이지만 배열이 아닐 때."""
        reply = '{"key": "value"}'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a"], "영역", "상위")
            assert result is None

    def test_ai_split_empty_array(self, fresh_db):
        """빈 배열."""
        reply = '[]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a"], "영역", "상위")
            assert result is None

    def test_ai_split_non_string_elements(self, fresh_db):
        """배열 원소가 문자열이 아닐 때: str() 변환."""
        reply = '[123, true, {"obj": "ect"}]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a", "b", "c"], "영역", "상위")
            # dict를 str()로 변환하면 Python repr이 나온다
            assert result == ["123", "True", "{'obj': 'ect'}"]

    def test_ai_split_with_extra_text(self, fresh_db):
        """응답에 JSON 외 텍스트가 있을 때: rindex/index로 추출."""
        reply = '여기 설명이 있고\n["항목1", "항목2"]\n추가 텍스트'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a", "b"], "영역", "상위")
            assert result == ["항목1", "항목2"]

    def test_ai_split_whitespace_trim(self, fresh_db):
        """배열 원소의 공백 제거."""
        reply = '[" 항목1 ", "  항목2  "]'
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = reply
            result = _ai_split("상위", ["a", "b"], "영역", "상위")
            assert result == ["항목1", "항목2"]


class TestAiInsights:
    """_ai_insights 함수: AI 폴백 로직."""

    def test_ai_insights_success(self, fresh_db, real_integrations):
        """AI 활성화: 완료 호출."""
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = "AI 제안문"
            result = analytics._ai_insights(
                {"avg_done": 80, "pd_pct": 75, "streak": 10},
                [{"label": "월", "pct": 80, "planned": 5}],
                [{"label": "B1", "pct": 70, "planned": 3}],
                [{"name": "일", "pct": 40}],
            )
            assert result == "AI 제안문"

    def test_ai_insights_returns_none_when_ai_disabled(self, fresh_db, real_integrations):
        """AI 미설정이면 None. docstring 이 약속한 그대로다.

        여기에 규칙기반 폴백을 넣으면 안 된다. 사용자가 'AI 제안 받기'를 눌렀는데
        규칙기반 문장을 AI 답인 것처럼 돌려주는 셈이 되기 때문이다.
        규칙기반 개선점은 아래 테스트처럼 /analytics 화면이 늘 따로 보여 준다.
        """
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = None
            result = analytics._ai_insights(
                {"avg_done": 45, "pd_pct": 30, "streak": 2},
                [{"label": "월", "pct": 50, "planned": 3}],
                [{"label": "B1", "pct": 50, "planned": 3}],
                [],
            )
            assert result is None

    def test_analytics_page_always_shows_rule_based_insights(self, client):
        """AI 가 꺼져 있어도 /analytics 는 규칙기반 개선점을 늘 보여 준다.

        app/routes/analytics.py:308 이 _build_insights 를 무조건 계산한다.
        그래서 'AI 폴백이 없으면 사용자가 아무 제안도 못 받는다'는 것은 사실이 아니다.
        """
        r = client.get("/analytics")
        assert r.status_code == 200
        assert "개선점" in r.text

    def test_analytics_ai_button_reports_failure_clearly(self, client):
        """AI 가 꺼진 상태에서 버튼을 누르면 조용히 넘어가지 않고 이유를 알려 준다."""
        r = client.post("/analytics/ai", data={"rng": "7"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is False
        assert body.get("error")

    def test_ai_insights_empty_metrics(self, fresh_db, real_integrations):
        """빈 데이터 처리."""
        with unittest.mock.patch.object(ai, "complete") as mock:
            mock.return_value = "그대로 진행하세요"
            result = analytics._ai_insights(
                {"avg_done": 0, "pd_pct": 0, "streak": 0},
                [],
                [],
                [],
            )
            assert result == "그대로 진행하세요"
