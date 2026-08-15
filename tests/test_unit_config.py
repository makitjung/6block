# config.py 단위 테스트. 하루 골격(8블록)과 30분 슬롯 생성이 무너지지 않는지 본다.
import pytest

from app.config import (
    CORE_BLOCKS,
    DAY_BLOCKS,
    DEFAULT_SETTINGS,
    TONE_KEYS,
    TONES,
    WEEK_CORE_BLOCKS,
    area_tone,
    cat_tone,
    hhmm_to_min,
    slots_for_day,
)


def test_hhmm_to_min_정상값():
    assert hhmm_to_min("00:00") == 0
    assert hhmm_to_min("07:30") == 450
    assert hhmm_to_min("23:59") == 1439


@pytest.mark.parametrize("bad", ["7:30", "", "abc"])
def test_hhmm_to_min_은_숫자가_아니면_예외(bad):
    with pytest.raises((ValueError, IndexError)):
        hhmm_to_min(bad)


@pytest.mark.parametrize("loose,expected", [("0730", 420), ("24:0", 1440), ("99:99", 6039)])
def test_hhmm_to_min_은_범위를_확인하지_않는다(loose, expected):
    """자리만 잘라 쓰므로 24시 이상도 그냥 통과한다.

    설정 저장(/settings/blocktimes)이 _valid_hhmm 으로 앞에서 막고 있어 현재는 여기까지
    오지 않는다. 그 검증을 걷어내면 곧바로 이상한 슬롯이 만들어진다는 뜻이다.
    """
    assert hhmm_to_min(loose) == expected


def test_기본_블록_구성():
    assert len(DAY_BLOCKS) == 8
    assert CORE_BLOCKS == ["B1", "B2", "B3", "B4", "B5", "B6"]
    assert WEEK_CORE_BLOCKS == 42


def test_기본_블록은_시간이_이어지고_겹치지_않는다():
    for (_l1, _c1, _s1, e1), (_l2, _c2, s2, _e2) in zip(DAY_BLOCKS, DAY_BLOCKS[1:]):
        assert e1 == s2, f"{e1} 다음이 {s2} 라 틈이 있거나 겹친다"


def test_기본_설정_슬롯은_30분_연속이고_인덱스가_순차적이다():
    slots = slots_for_day()
    assert slots, "기본 설정에서 슬롯이 하나도 안 나온다"
    assert [s[0] for s in slots] == list(range(len(slots)))
    for idx, label, start, end in slots:
        assert hhmm_to_min(end) - hhmm_to_min(start) == 30, f"{label} {start}~{end} 가 30분이 아니다"
    for prev, cur in zip(slots, slots[1:]):
        assert prev[3] == cur[2], f"{prev[3]} 다음이 {cur[2]} 라 슬롯이 끊긴다"


def test_모든_블록이_슬롯을_최소_하나는_가진다():
    labels = {s[1] for s in slots_for_day()}
    assert labels == {b[0] for b in DAY_BLOCKS}


# 아래 세 가지는 slots_for_day 자체는 막지 않는 입력이다. 지금은 /settings/blocktimes 가
# 30분 배수·역순·형식을 400으로 거절해 도달할 수 없다(tests/test_flows.py 에서 확인).
# 그 검증이 유일한 방어선이라는 사실을 여기 못 박아 둔다.


def test_30분_배수가_아닌_블록은_슬롯이_경계를_넘는다():
    slots = slots_for_day([("B1", True, "07:30", "07:50")])
    assert slots and slots[-1][3] == "08:00", "블록은 07:50 에 끝나는데 슬롯은 08:00 까지 간다"


def test_역순_시간_블록은_슬롯이_0개가_된다():
    assert slots_for_day([("B1", True, "10:00", "08:00")]) == []


def test_자정을_넘는_블록도_슬롯이_0개가_된다():
    assert slots_for_day([("B6", True, "23:00", "01:00")]) == []


def test_슬롯_생성이_무한루프에_빠지지_않는다():
    """같은 시각으로 시작·종료를 주면 길이 0이다. 무한루프면 여기서 멈춘다."""
    assert slots_for_day([("B1", True, "09:00", "09:00")]) == []


def test_색_톤_팔레트가_서로_맞다():
    assert TONE_KEYS == {k for k, _ in TONES}
    assert cat_tone("코어") == "blue"
    assert cat_tone("없는이름") == "black"
    assert all(area_tone(i) in TONE_KEYS for i in range(20))


def test_기본_설정값은_전부_문자열():
    """app_settings 는 TEXT 컬럼이라 숫자·불리언이 섞이면 비교가 어긋난다."""
    for key, val in DEFAULT_SETTINGS.items():
        assert isinstance(val, str), f"{key} 기본값이 문자열이 아니다: {val!r}"
