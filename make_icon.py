"""Draw Plinth's icons: a bronze bust on a stone plinth, under a spotlight.

Run once, and again only when the design changes; the output is committed.
It needs Pillow, which the page build does not, so the nightly job never runs
this - board.py simply serves the files that are here.

  icon-512.png, icon-192.png   the app tile, rounded, for browsers and "any"
  icon-maskable-512.png        full-bleed, content inside the 80% safe circle,
                               for Android home screens that crop to a shape
  icon-180.png                 full-bleed, for iPhone home screens
  icon.ico                     16-256 px, for the Windows desktop and Start menu
  icon-refresh.ico             the same with a refresh badge, for the shortcut
                               that updates everything

Run: python make_icon.py
"""

import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024                      # design units
K = 4                         # supersampling: drawn at 4096, then reduced

BG_TOP = (44, 101, 87)
BG_BOTTOM = (16, 52, 45)
SPOT = (92, 160, 139)
STONE_LIGHT = (246, 241, 232)
STONE_MID = (226, 218, 204)
STONE_DARK = (190, 180, 163)
BRONZE_LIGHT = (236, 182, 112)
BRONZE_MID = (196, 132, 64)
BRONZE_DARK = (116, 70, 32)
BADGE = (246, 241, 232)
BADGE_INK = (24, 70, 60)


def u(value):
    return int(round(value * K))


def box(x0, y0, x1, y1):
    return [u(x0), u(y0), u(x1), u(y1)]


def canvas():
    return Image.new("RGBA", (S * K, S * K), (0, 0, 0, 0))


def mask():
    return Image.new("L", (S * K, S * K), 0)


def gradient(size, top, bottom, angle=0):
    """A two-colour gradient filling `size`, rotated by `angle` degrees."""
    ramp = Image.linear_gradient("L").resize((size[0] * 2, size[1] * 2))
    if angle:
        ramp = ramp.rotate(angle, resample=Image.BICUBIC, expand=False)
    ramp = ramp.crop((size[0] // 2, size[1] // 2,
                      size[0] // 2 + size[0], size[1] // 2 + size[1]))
    return Image.composite(Image.new("RGBA", size, bottom + (255,)),
                           Image.new("RGBA", size, top + (255,)), ramp)


def paint(layer, shape, top, bottom, angle=0):
    """Fill the white of `shape` with a gradient, onto `layer`."""
    fill = gradient(layer.size, top, bottom, angle)
    layer.paste(fill, (0, 0), shape)


def background(full_bleed):
    tile = canvas()
    shape = mask()
    d = ImageDraw.Draw(shape)
    if full_bleed:
        d.rectangle([0, 0, S * K, S * K], fill=255)
    else:
        d.rounded_rectangle(box(0, 0, S, S), radius=u(228), fill=255)
    paint(tile, shape, BG_TOP, BG_BOTTOM)

    # A gallery spotlight on the piece, and the pool it leaves on the floor.
    # Blurred shapes rather than a radial gradient: the gradient's square
    # left straight edges in the light.
    light = mask()
    d = ImageDraw.Draw(light)
    d.ellipse(box(212, 60, 812, 700), fill=150)
    d.ellipse(box(250, 840, 774, 960), fill=90)
    light = light.filter(ImageFilter.GaussianBlur(u(120)))
    light = ImageChops.multiply(light, shape)
    tile.paste(Image.new("RGBA", tile.size, SPOT + (255,)), (0, 0), light)
    return tile, shape


# A classical head in profile, facing left, with neck and chest cut to sit on
# a socle - the way a head is drawn on a coin. A frontal head on a dome read
# as a chess pawn, or a blank "user" avatar; a profile reads as sculpture.
BUST = [
    # crown, round the back of the skull to the nape
    (500, 148), (556, 150), (604, 176), (630, 222), (636, 276), (624, 326),
    (602, 360), (590, 392),
    # neck to the back shoulder, and the flat cut the bust stands on - with
    # enough points at the corners that the curve cannot overshoot into spikes
    (596, 424), (636, 446), (676, 470), (696, 504), (700, 532), (692, 548),
    (670, 553), (600, 553), (520, 553), (440, 553), (380, 552), (358, 542),
    (350, 520), (364, 488),
    # chest, throat, chin, lips, and the straight Greek nose up to the brow
    (410, 462), (446, 444), (458, 414), (456, 386), (440, 374), (420, 366),
    (414, 352), (418, 340), (408, 331), (413, 322), (404, 312), (398, 304),
    (382, 294), (392, 280), (404, 262), (412, 242), (416, 220), (430, 190),
    (458, 162),
]

# Hair, dressed back from the brow over the ear to the nape.
HAIRLINE = [(438, 178), (470, 196), (500, 206), (522, 226), (540, 252),
            (548, 282), (562, 312), (584, 340), (604, 362)]
CROWN = [(624, 326), (636, 276), (630, 222), (604, 176), (556, 150), (500, 148),
         (458, 162)]


def smooth(points, steps=10):
    """A closed Catmull-Rom curve through every point."""
    out, n = [], len(points)
    for i in range(n):
        p0, p1, p2, p3 = (points[(i + k) % n] for k in (-1, 0, 1, 2))
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                       + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                       + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                for j in (0, 1)))
    return out


def sculpture(scale=1.0, lift=0.0):
    """The bust and its plinth, as one layer. `scale` shrinks toward centre."""
    art = canvas()

    def at(x0, y0, x1, y1):
        c = 512
        return box(c + (x0 - c) * scale, c + (y0 - c) * scale + lift,
                   c + (x1 - c) * scale, c + (y1 - c) * scale + lift)

    def pt(x, y):
        c = 512
        return (u(c + (x - c) * scale), u(c + (y - c) * scale + lift))

    # Soft shadow where the plinth meets the floor.
    shadow = mask()
    ImageDraw.Draw(shadow).ellipse(at(300, 884, 724, 936), fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(u(14)))
    art.paste(Image.new("RGBA", art.size, (0, 0, 0, 255)), (0, 0), shadow)

    # The plinth: base, body, cap. Light from the upper left.
    for coords, top, bottom, angle in (
            (at(318, 868, 706, 906), STONE_MID, STONE_DARK, 0),
            (at(350, 612, 674, 872), STONE_LIGHT, STONE_DARK, 90),
            (at(322, 576, 702, 616), STONE_LIGHT, STONE_MID, 0)):
        shape = mask()
        ImageDraw.Draw(shape).rounded_rectangle(coords, radius=u(5 * scale),
                                                fill=255)
        paint(art, shape, top, bottom, angle)

    # The bust and its turned socle: one bronze casting.
    bronze = mask()
    d = ImageDraw.Draw(bronze)
    d.polygon([pt(x, y) for x, y in smooth(BUST)], fill=255)
    d.polygon([pt(452, 578), pt(592, 578), pt(574, 562), pt(470, 562)], fill=255)
    d.rectangle(at(482, 546, 562, 564), fill=255)
    paint(art, bronze, BRONZE_LIGHT, BRONZE_DARK, 40)

    # The hair: a darker patina, with the waves a chisel leaves.
    hair = mask()
    h = ImageDraw.Draw(hair)
    h.polygon([pt(x, y) for x, y in smooth(HAIRLINE + CROWN, steps=6)], fill=255)
    hair = ImageChops.multiply(hair, bronze)
    paint(art, hair, BRONZE_MID, BRONZE_DARK, 30)
    waves = mask()
    w = ImageDraw.Draw(waves)
    for offset in (0, 30, 60, 90):
        w.arc(at(470 + offset, 150 + offset * 0.6, 640 + offset * 0.4,
                 330 + offset * 0.5), start=200, end=290, fill=110, width=u(7))
    waves = ImageChops.multiply(waves.filter(ImageFilter.GaussianBlur(u(2))), hair)
    art.paste(Image.new("RGBA", art.size, BRONZE_DARK + (255,)), (0, 0), waves)

    # The ear, and the shadow under the jaw that gives the head its weight.
    ear = mask()
    ImageDraw.Draw(ear).ellipse(at(552, 262, 588, 318), fill=255)
    paint(art, ear, BRONZE_LIGHT, BRONZE_MID, 20)
    inner = mask()
    ImageDraw.Draw(inner).arc(at(560, 272, 582, 308), start=250, end=110,
                              fill=140, width=u(5))
    art.paste(Image.new("RGBA", art.size, BRONZE_DARK + (255,)), (0, 0), inner)
    jaw = mask()
    ImageDraw.Draw(jaw).ellipse(at(458, 372, 590, 430), fill=120)
    jaw = ImageChops.multiply(jaw.filter(ImageFilter.GaussianBlur(u(14))), bronze)
    art.paste(Image.new("RGBA", art.size, BRONZE_DARK + (255,)), (0, 0), jaw)

    # Where the spotlight catches the metal: brow, cheek, the front shoulder.
    shine = mask()
    s = ImageDraw.Draw(shine)
    s.ellipse(at(410, 196, 480, 262), fill=120)
    s.ellipse(at(428, 290, 486, 346), fill=70)
    s.ellipse(at(362, 470, 456, 530), fill=90)
    shine = ImageChops.multiply(shine.filter(ImageFilter.GaussianBlur(u(18))), bronze)
    art.paste(Image.new("RGBA", art.size, (255, 238, 204, 255)), (0, 0), shine)

    # The eye, as a sculptor cuts it: a shadowed socket, no pupil.
    eye = mask()
    ImageDraw.Draw(eye).ellipse(at(418, 262, 444, 276), fill=150)
    eye = ImageChops.multiply(eye.filter(ImageFilter.GaussianBlur(u(3))), bronze)
    art.paste(Image.new("RGBA", art.size, BRONZE_DARK + (255,)), (0, 0), eye)
    return art


def refresh_badge(tile):
    """A round badge with a circular arrow, bottom right."""
    d = ImageDraw.Draw(tile)
    cx = cy = 836
    d.ellipse(box(672, 672, 1000, 1000), fill=BADGE_INK + (255,))
    d.ellipse(box(690, 690, 982, 982), fill=BADGE + (255,))
    radius, width = 86, 30
    start, end = 300, 235        # clockwise, leaving a gap at the top
    d.arc(box(cx - radius, cy - radius, cx + radius, cy + radius),
          start=start, end=end, fill=BADGE_INK + (255,), width=u(width))
    # The arrowhead sits on the arc's end and points on round the circle.
    theta = math.radians(end)
    mid = radius - width / 2
    px, py = cx + mid * math.cos(theta), cy + mid * math.sin(theta)
    tx, ty = -math.sin(theta), math.cos(theta)          # clockwise tangent
    nx, ny = math.cos(theta), math.sin(theta)
    d.polygon([(u(px + 50 * tx), u(py + 50 * ty)),
               (u(px + 34 * nx), u(py + 34 * ny)),
               (u(px - 34 * nx), u(py - 34 * ny))], fill=BADGE_INK + (255,))


def render(full_bleed=False, maskable=False, badge=False):
    tile, shape = background(full_bleed or maskable)
    # A maskable icon may be cropped to a circle: keep the art inside 80%.
    art = sculpture(scale=0.78 if maskable else 1.0, lift=6 if maskable else 0)
    tile = Image.alpha_composite(tile, art)
    if not (full_bleed or maskable):
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"), shape))
    if badge:
        refresh_badge(tile)
    return tile


def reduce(image, size):
    return image.resize((size, size), Image.LANCZOS)


def main():
    tile = render()
    reduce(tile, 512).save(os.path.join(HERE, "icon-512.png"), optimize=True)
    reduce(tile, 192).save(os.path.join(HERE, "icon-192.png"), optimize=True)
    reduce(render(maskable=True), 512).save(
        os.path.join(HERE, "icon-maskable-512.png"), optimize=True)
    reduce(render(full_bleed=True), 180).convert("RGB").save(
        os.path.join(HERE, "icon-180.png"), optimize=True)
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    reduce(tile, 256).save(os.path.join(HERE, "icon.ico"), sizes=sizes)
    reduce(render(badge=True), 256).save(os.path.join(HERE, "icon-refresh.ico"),
                                         sizes=sizes)
    print("wrote icon-512.png, icon-192.png, icon-maskable-512.png, "
          "icon-180.png, icon.ico, icon-refresh.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
