# QA 검증 항목 3: 적대적 반박 세션
# 다른 페르소나의 버그 보고를 검증하고 심각도를 재평가한다.

import pytest
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
        """중첩 대괄호가 정규식의 한계로 데이터를 손상시킨다. 버그 확정."""
        kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")

        # 실제 동작 확인
        assert kind == "고민"
        assert title == "] 제목"  # 손상됨

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

        현재 구현이 형식이 아닌 입력에 대해 부분 정규식 매칭으로
        데이터를 손상시키는 것은 docstring 위반이다.
        """
        # 기대: ("고민", "[고민 [부제]] 제목")
        # 실제: ("고민", "] 제목")
        # → 버그 확인됨

        kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")

        # 버그를 명시적으로 기록
        assert title != "[고민 [부제]] 제목", "형식이 아닌 입력에 대해 전체 문자열을 반환해야 함"
        assert title == "] 제목", "정규식이 중첩 대괄호를 처리하지 못해 손상됨"
