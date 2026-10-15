#!/usr/bin/env python3
"""用 assets/remote.png 產生 App 圖示：assets/icon.png (1024)、assets/PlayRemote.icns (macOS)、assets/PlayRemote.ico (Windows)。

macOS 圖示規格：1024 畫布、主體 824×824 圓角方形 (圓角約 185)、四周留白給陰影。
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "assets" / "remote.png"
S = 1024
BODY = 824
RADIUS = 185


def gradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return g.resize((size, size))


def main():
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    off = (S - BODY) // 2
    # 圓角方形背景 + 陰影
    mask = Image.new("L", (BODY, BODY), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BODY - 1, BODY - 1), RADIUS, fill=255)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sm = Image.new("L", (S, S), 0)
    sm.paste(mask, (off, off + 12))
    shadow.putalpha(sm.filter(ImageFilter.GaussianBlur(18)).point(lambda v: int(v * 0.35)))
    canvas = Image.alpha_composite(canvas, shadow)
    bg = gradient(BODY, (58, 112, 235), (24, 52, 150)).convert("RGBA")
    bg.putalpha(mask)
    canvas.paste(bg, (off, off), bg)

    # 遙控器：斜放、置中、帶陰影
    remote = Image.open(SRC).convert("RGBA")
    h = int(BODY * 0.98)
    w = int(remote.width * h / remote.height)
    remote = remote.resize((w, h), Image.LANCZOS).rotate(-28, resample=Image.BICUBIC, expand=True)
    rs = Image.new("RGBA", remote.size, (0, 0, 0, 0))
    rs.putalpha(remote.getchannel("A").filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.45)))
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cx, cy = (S - remote.width) // 2, (S - remote.height) // 2
    layer.paste(rs, (cx + 14, cy + 22), rs)
    layer.paste(remote, (cx, cy), remote)
    # 遙控器超出圓角方形的部分裁掉
    clip = Image.new("L", (S, S), 0)
    clip.paste(mask, (off, off))
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", (S, S), 0), clip))
    canvas = Image.alpha_composite(canvas, layer)

    out = HERE / "assets" / "icon.png"
    canvas.save(out)
    print("wrote", out)

    # .icns
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "PlayRemote.iconset"
        iconset.mkdir()
        for sz in (16, 32, 128, 256, 512):
            canvas.resize((sz, sz), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}.png")
            canvas.resize((sz * 2, sz * 2), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}@2x.png")
        icns = HERE / "assets" / "PlayRemote.icns"
        r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], capture_output=True)
        print("wrote", icns) if r.returncode == 0 else print("iconutil 不可用（非 macOS），略過 .icns")
    # .ico
    ico = HERE / "assets" / "PlayRemote.ico"
    canvas.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ico)


if __name__ == "__main__":
    main()
