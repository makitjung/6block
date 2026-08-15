# 극단 날짜(9999-12-31)로 하루 화면을 열 때 500 이 나는지 실제 요청으로 확인
import pytest


@pytest.mark.parametrize("d", ["9999-12-31", "9999-12-30", "2026-08-15"])
def test_day_view_extreme_date(client, d):
    r = client.get(f"/day/{d}")
    assert r.status_code == 200, f"{d} → {r.status_code}"
