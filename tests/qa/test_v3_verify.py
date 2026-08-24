# QA 검증 항목 3: 적대적 반박 세션
# 다른 페르소나의 버그 보고를 검증하고 심각도를 재평가한다.

import app.integrations.gcal_write as gcal_write


class TestParseSummaryNestedBracketsVerification:
    """
    항목 1: parse_summary: 중첩 대괄호 입력 시 제목 손상

    보고된 심각도: High
    기대 동작: [고민 [부제]] 제목 (형식 미준수) → ("고민", "[고민 [부제]] 제목")
    실제 동작: ("고민", "] 제목") — 사용자 입력 손상

    재현: tests/qa/test_p2_integrations.py::TestGcalWriteParseSummaryEdges::test_parse_summary_nested_brackets
    """

    def test_nested_brackets_reproduced_confirmed(self):
        """(수정 후) 중첩 대괄호는 형식이 아니므로 제목이 통째로 보존된다."""
        kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")

        assert kind == "고민"
        assert title == "[고민 [부제]] 제목"      # 더 이상 손상되지 않는다

    def test_normal_format_works_correctly(self):
        """대조군: 정상 형식은 올바르게 작동."""
        kind, title = gcal_write.parse_summary("[고민] 제목")
        assert kind == "고민"
        assert title == "제목"

    def test_no_format_returns_full_string(self):
        """대조군: 형식이 아니면 통째로 반환."""
        kind, title = gcal_write.parse_summary("제목만 있고 괄호 없음")
        assert kind == "고민"
        assert title == "제목만 있고 괄호 없음"

    def test_docstring_expected_behavior_for_non_matching_format(self):
        """
        Docstring 검증: "'[종류] 제목' → (kind, title). 형식이 아니면 (고민, 통째 제목)."

        중첩 대괄호는 명시된 형식 `[종류] 제목` 이 아니다.
        따라서 "형식이 아님" 규칙을 따라 통째 문자열을 반환해야 한다.

        보고 당시 구현은 형식이 아닌 입력에 부분 정규식 매칭을 해 데이터를 손상시켰다.
        수정 후에는 이 규칙을 지킨다. 종류 자리에 대괄호를 허용하지 않는 정규식으로 바꿔
        중첩 입력은 아예 매칭되지 않고 통째 제목으로 떨어진다.
        """
        kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")

        assert kind == "고민"
        assert title == "[고민 [부제]] 제목", "형식이 아닌 입력은 전체 문자열을 반환해야 함"
