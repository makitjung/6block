# 6block 앱 아이콘을 코드로 그린다(후보 미리보기 → 고른 안으로 png/ico/svg 생성)
#
# 미리보기 · .venv/bin/python scripts/make_icons.py --preview
#   → /tmp 아래에 후보 3안과 비교 시트를 만든다(앱 파일은 건드리지 않는다).
# 확정   · .venv/bin/python scripts/make_icons.py --build grid|day|dial
#   → app/static 에 icon.png(512) · apple-touch-icon.png(180) · favicon.ico ·
#     icon.svg · icon-maskable.svg 를 덮어쓴다.
#
# 도안은 모두 가운데 80% 안에 들어가 maskable 로 잘려도 안 깨진다.
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
PREVIEW_DIR = Path("/tmp/6block-icons")

SS = 4  # 슈퍼샘플 배율. 크게 그리고 줄여 계단현상을 없앤다.

# 앱 색과 맞춘다(style.css 의 --accent, --tone-blue 계열).
BG_TOP = (27, 30, 38)
BG_BOTTOM = (10, 11, 14)
IDLE = (39, 43, 54)
HI_FROM = (165, 243, 252)
HI_TO = (56, 189, 248)
ACCENT = (26, 115, 232)


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _gradient(size, top, bottom, diagonal=True):
    """작은 그라데이션을 만들어 키운다(픽셀 루프 없이 부드럽게)."""
    n = 64
    src = Image.new("RGB", (n, n))
    px = src.load()
    for y in range(n):
        for x in range(n):
            t = ((x + y) / (2 * (n - 1))) if diagonal else (y / (n - 1))
            px[x, y] = _lerp(top, bottom, t)
    return src.resize((size, size), Image.BICUBIC)


def _hi_fill(draw_size, box):
    """강조 도형에 쓸 대각 그라데이션 조각(하늘색 → 파랑)."""
    return _gradient(draw_size, HI_FROM, HI_TO)


def _paste_masked(base, fill_img, mask):
    base.paste(fill_img, (0, 0), mask)


def draw_grid(size):
    """A안 · 격자. 하루 6블록을 2행 3열로 두고 지금 블록 하나만 밝힌다(현행 계승)."""
    s = size * SS
    img = _gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)
    # 가운데 80%(=0.1~0.9) 안에 6칸을 배치한다.
    pad, gap = 0.14 * s, 0.045 * s
    cw = (s - pad * 2 - gap * 2) / 3
    ch = (s - pad * 2 - gap) / 2
    r = cw * 0.19
    hi_mask = Image.new("L", (s, s), 0)
    hm = ImageDraw.Draw(hi_mask)
    for row in range(2):
        for col in range(3):
            x0 = pad + col * (cw + gap)
            y0 = pad + row * (ch + gap)
            box = (x0, y0, x0 + cw, y0 + ch)
            if (row, col) == (1, 1):           # 아래 가운데 = 지금 블록
                hm.rounded_rectangle(box, radius=r, fill=255)
            else:
                d.rounded_rectangle(box, radius=r, fill=IDLE)
    _paste_masked(img, _gradient(s, HI_FROM, HI_TO), hi_mask)
    return img.resize((size, size), Image.LANCZOS)


def draw_day(size):
    """B안 · 하루 띠. 세로 막대 6개가 왼쪽부터 차오르고 지금 블록이 밝다."""
    s = size * SS
    img = _gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad, gap = 0.13 * s, 0.035 * s
    bw = (s - pad * 2 - gap * 5) / 6
    r = bw * 0.42
    base_y1 = s - pad
    # 왼쪽 3개는 지난 블록(길게 참), 4번째가 지금, 나머지는 아직 빈 블록.
    heights = [0.62, 0.74, 0.56, 0.80, 0.30, 0.30]
    hi_mask = Image.new("L", (s, s), 0)
    hm = ImageDraw.Draw(hi_mask)
    for i, h in enumerate(heights):
        x0 = pad + i * (bw + gap)
        y0 = base_y1 - (s - pad * 2) * h
        box = (x0, y0, x0 + bw, base_y1)
        if i == 3:
            hm.rounded_rectangle(box, radius=r, fill=255)
        else:
            d.rounded_rectangle(box, radius=r, fill=IDLE)
    _paste_masked(img, _gradient(s, HI_FROM, HI_TO), hi_mask)
    return img.resize((size, size), Image.LANCZOS)


def draw_dial(size):
    """C안 · 다이얼. 포모도로 링을 6등분하고 한 조각만 밝힌다(작게 줄여도 알아본다)."""
    s = size * SS
    img = _gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)
    cx = cy = s / 2
    outer = 0.38 * s            # 가운데 76% 안 → maskable 안전
    width = 0.115 * s
    box = (cx - outer, cy - outer, cx + outer, cy + outer)
    seg, gap_deg = 360 / 6, 7
    hi_mask = Image.new("L", (s, s), 0)
    hm = ImageDraw.Draw(hi_mask)
    for i in range(6):
        a0 = -90 + i * seg + gap_deg / 2
        a1 = -90 + (i + 1) * seg - gap_deg / 2
        if i == 2:              # 오른쪽 아래 조각 = 지금 블록
            hm.arc(box, a0, a1, fill=255, width=round(width))
        else:
            d.arc(box, a0, a1, fill=IDLE, width=round(width))
    _paste_masked(img, _gradient(s, HI_FROM, HI_TO), hi_mask)
    # 가운데 점 하나로 '지금'을 찍는다.
    rr = 0.075 * s
    d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


CANDIDATES = {"grid": draw_grid, "day": draw_day, "dial": draw_dial}
LABELS = {"grid": "A · 격자", "day": "B · 하루 띠", "dial": "C · 다이얼"}


# -- SVG (같은 도안을 벡터로도 남겨 어떤 크기에서도 다시 그려진다) ------------

_SVG_DEFS = (
    '<defs>'
    '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#1B1E26"/><stop offset="100%" stop-color="#0A0B0E"/>'
    '</linearGradient>'
    '<linearGradient id="hi" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#A5F3FC"/><stop offset="100%" stop-color="#38BDF8"/>'
    '</linearGradient>'
    '</defs>'
)


def _svg(body: str, rounded: bool) -> str:
    bg = ('<rect width="512" height="512" rx="112" fill="url(#bg)"/>' if rounded
          else '<rect width="512" height="512" fill="url(#bg)"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        'width="512" height="512">' + _SVG_DEFS + bg + body + '</svg>\n'
    )


def _svg_body(name: str) -> str:
    S = 512.0
    out = []
    if name == "grid":
        pad, gap = 0.14 * S, 0.045 * S
        cw = (S - pad * 2 - gap * 2) / 3
        ch = (S - pad * 2 - gap) / 2
        r = cw * 0.19
        for row in range(2):
            for col in range(3):
                x, y = pad + col * (cw + gap), pad + row * (ch + gap)
                fill = "url(#hi)" if (row, col) == (1, 1) else "#272B36"
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw:.1f}" '
                           f'height="{ch:.1f}" rx="{r:.1f}" fill="{fill}"/>')
    elif name == "day":
        pad, gap = 0.13 * S, 0.035 * S
        bw = (S - pad * 2 - gap * 5) / 6
        r = bw * 0.42
        for i, h in enumerate([0.62, 0.74, 0.56, 0.80, 0.30, 0.30]):
            x = pad + i * (bw + gap)
            hh = (S - pad * 2) * h
            y = S - pad - hh
            fill = "url(#hi)" if i == 3 else "#272B36"
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                       f'height="{hh:.1f}" rx="{r:.1f}" fill="{fill}"/>')
    else:  # dial
        import math

        cx = cy = S / 2
        rad, width = 0.38 * S, 0.115 * S
        seg, gap_deg = 60.0, 7.0
        for i in range(6):
            a0 = math.radians(-90 + i * seg + gap_deg / 2)
            a1 = math.radians(-90 + (i + 1) * seg - gap_deg / 2)
            x0, y0 = cx + rad * math.cos(a0), cy + rad * math.sin(a0)
            x1, y1 = cx + rad * math.cos(a1), cy + rad * math.sin(a1)
            stroke = "url(#hi)" if i == 2 else "#272B36"
            out.append(
                f'<path d="M {x0:.1f} {y0:.1f} A {rad:.1f} {rad:.1f} 0 0 1 '
                f'{x1:.1f} {y1:.1f}" fill="none" stroke="{stroke}" '
                f'stroke-width="{width:.1f}" stroke-linecap="round"/>'
            )
        out.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{0.075 * S:.1f}" fill="#1A73E8"/>')
    return "".join(out)


def preview():
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name, fn in CANDIDATES.items():
        big = fn(256)
        big.save(PREVIEW_DIR / f"{name}-256.png")
        fn(64).save(PREVIEW_DIR / f"{name}-64.png")
        tiles.append((name, big, fn(64)))
    # 비교 시트 · 큰 것 아래에 폰 홈화면 크기(64)를 함께 둔다.
    pad, w, h = 28, 256, 256 + 28 + 64
    sheet = Image.new("RGB", (pad + (w + pad) * 3, h + pad * 2), (245, 246, 248))
    for i, (_name, big, small) in enumerate(tiles):
        x = pad + i * (w + pad)
        sheet.paste(big, (x, pad))
        sheet.paste(small, (x + (w - 64) // 2, pad + 256 + 28))
    out = PREVIEW_DIR / "candidates.png"
    sheet.save(out)
    for name in CANDIDATES:
        print(f"{name} ({LABELS[name]}) -> {PREVIEW_DIR / (name + '-256.png')}")
    print(f"sheet -> {out}")


def build(name: str):
    fn = CANDIDATES[name]
    # 홈화면 아이콘은 불투명해야 한다(iOS는 투명을 검게 깔아 버린다).
    fn(512).convert("RGB").save(STATIC / "icon.png")
    fn(180).convert("RGB").save(STATIC / "apple-touch-icon.png")
    fn(64).convert("RGBA").save(
        STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    body = _svg_body(name)
    (STATIC / "icon.svg").write_text(_svg(body, rounded=True), encoding="utf-8")
    # maskable 은 모서리를 깎지 않는다(런처가 제 모양으로 자른다).
    (STATIC / "icon-maskable.svg").write_text(_svg(body, rounded=False), encoding="utf-8")
    for f in ("icon.png", "apple-touch-icon.png", "favicon.ico", "icon.svg",
              "icon-maskable.svg"):
        print(f"[ok] {STATIC / f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--preview"]:
        preview()
    elif args[:1] == ["--build"] and len(args) == 2 and args[1] in CANDIDATES:
        build(args[1])
    else:
        print(__doc__ or "")
        print("usage: make_icons.py --preview | --build {grid|day|dial}")
        sys.exit(2)
