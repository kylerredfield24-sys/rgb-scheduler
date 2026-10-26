"""
RGB Scheduler
Switches ASRock Polychrome (ASRRGBLED) fan lights to the colors you schedule.

How it works:
  1. Opens ASRRGBLED (if it isn't open) far off the screen, so you never see it.
  2. Presses "Addressable RGB LED" by name (Windows UI Automation, no mouse).
  3. Sets the color, trying in order:
       a. Off mode / Static mode + RGB number boxes (exact)
       b. Looks at the window, finds the color wheel and shade box by their colors,
          and clicks green on the wheel or black in the shade box (hidden clicks)
       c. If hidden clicks don't register, a quick real click (optional)
  4. Presses Apply and closes ASRRGBLED.

Windows only. Run build.bat to turn it into "RGB Scheduler.exe".
"""

import ctypes
import datetime as dt
import glob
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from xml.sax.saxutils import escape

WIN = os.name == "nt"

if WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
    user32.ChildWindowFromPointEx.restype = wintypes.HWND
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
else:  # lets the image-finding code be tested off Windows
    user32 = gdi32 = kernel32 = None

APP_NAME = "RGB Scheduler"
APP_VERSION = "1.2"                               # 1.9 -> 2.0, never 1.10
GITHUB_REPO = "kylerredfield24-sys/rgb-scheduler"  # where updates are published (Releases)
UPDATE_ASSET = "RGB-Scheduler.exe"
TASK_NAME = "RGB Scheduler"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RGBScheduler")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
ICON_PATH = os.path.join(CONFIG_DIR, "icon.ico")

GREEN = "#22c55e"
BG = "#0f1115"
CARD = "#171a21"
CARD_BORDER = "#252a34"
MUTED = "#8b93a1"

PRESETS = [  # quick picks in the editor
    ("RAM Green", "#008600"), ("Green", "#00FF00"), ("Red", "#FF0000"), ("Blue", "#0000FF"), ("Purple", "#8000FF"),
    ("Cyan", "#00FFFF"), ("Yellow", "#FFFF00"), ("Orange", "#FF6A00"), ("Pink", "#FF00AA"),
    ("White", "#FFFFFF"), ("Off (black)", "#000000"),
]

DEFAULTS = {
    "enabled": True,
    "schedule": [   # each entry: a time and the color the fans switch to
        {"id": "a1", "time": "10:00", "color": "#00FF00", "on": True},
        {"id": "a2", "time": "21:00", "color": "#000000", "on": True},
    ],
    "asr_path": "",
    "header_name": "Addressable",
    "apply_name": "Apply All",
    "close_after": False,     # keep Polychrome running hidden: faster, no popups
    "apply_on_launch": True,
    "start_with_windows": False,
    "allow_visible_clicks": False,
    "click_method": "auto",  # learned: "post" (hidden) or "real"
    "_last_color": "",
    "auto_update_check": True,
}


def hex_rgb(hexc):
    c = hexc.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def is_black(hexc):
    return max(hex_rgb(hexc)) < 12


def color_name(hexc):
    hexc = hexc.upper()
    for n, h in PRESETS:
        if h == hexc:
            return n
    return hexc


# ================================================================ admin / startup

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def launch_command():
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    prog = pyw if os.path.exists(pyw) else sys.executable
    return prog, f'"{os.path.abspath(__file__)}"'


def relaunch_as_admin():
    prog, args = launch_command()
    extra = " ".join(f'"{a}"' for a in sys.argv[1:])
    params = f"{args} {extra}".strip()
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", prog, params, None, 1) > 32


LAUNCH_TASK = "RGB Scheduler (open as admin)"
SHOW_EVENT = "Local\\RGBSchedulerShow"
NO_WINDOW = 0x08000000


def _register_task(name, args, at_logon, multiple):
    """Save a Task Scheduler task that runs this app with admin rights. Windows doesn't
    ask "Yes/No" for these, so the app opens as admin without the prompt."""
    prog, base = launch_command()
    args = f"{base} {args}".strip()
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    trigger = (f"<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId>"
               f"<Delay>PT20S</Delay></LogonTrigger></Triggers>") if at_logon else "<Triggers/>"
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(name)}</Description></RegistrationInfo>
  {trigger}
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>{multiple}</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(prog)}</Command><Arguments>{escape(args)}</Arguments></Exec>
  </Actions>
</Task>"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    xml_path = os.path.join(CONFIG_DIR, "task.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml)
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/XML", xml_path],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def ensure_launch_task():
    """(Admin only) Keep the "open as admin" task pointing at this copy of the app."""
    return _register_task(LAUNCH_TASK, "", at_logon=False, multiple="Parallel")


def launch_task_points_here():
    """True if the saved "open as admin" task starts THIS copy of the app. (If you download
    a new copy somewhere else, the old task would start the old copy, or nothing.)"""
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", LAUNCH_TASK, "/XML"],
                           capture_output=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            return False
        raw = r.stdout
        text = raw.decode("utf-16", errors="ignore") if b"\x00" in raw[:200] \
            else raw.decode("utf-8", errors="ignore")
        m = re.search(r"<Command>(.*?)</Command>", text, re.S)
        if not m:
            return False
        from xml.sax.saxutils import unescape
        prog, _ = launch_command()
        return os.path.normcase(os.path.abspath(unescape(m.group(1).strip()))) == \
            os.path.normcase(os.path.abspath(prog))
    except Exception:
        return False


def open_through_task():
    """(Not admin) Ask Task Scheduler to open the app as admin. No Yes/No prompt."""
    if not launch_task_points_here():
        return False
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", LAUNCH_TASK],
                           capture_output=True, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def set_startup(enable):
    """Start at logon with admin rights through Task Scheduler (no UAC prompt)."""
    if not enable:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                       capture_output=True, creationflags=NO_WINDOW)
        return True, ""
    return _register_task(TASK_NAME, "--minimized", at_logon=True, multiple="IgnoreNew")


# ================================================================ config

def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if "schedule" not in saved and "on_time" in saved:   # settings from the older version
            saved["schedule"] = [
                {"id": "a1", "time": saved.get("on_time", "10:00"), "color": saved.get("on_color", "#00FF00"), "on": True},
                {"id": "a2", "time": saved.get("off_time", "21:00"), "color": "#000000", "on": True},
            ]
        cfg.update(saved)
    except Exception:
        pass
    tr = cfg.get("toggle_rel")
    if tr and not (1.05 <= tr[1] <= 1.65):
        cfg.pop("toggle_rel", None)
    if not cfg.get("_v4"):
        cfg["close_after"] = False
        cfg["_v4"] = True
    if not cfg.get("_v3"):
        cfg["allow_visible_clicks"] = False    # real clicks move your mouse: off unless you turn it on
        cfg["click_method"] = "auto"
        cfg["_v3"] = True
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ================================================================ icon

def make_icon_image(size=256):
    from PIL import Image, ImageDraw, ImageFilter
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=(17, 20, 27, 255))
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    m = int(s * 0.16)
    gd.ellipse([m, m, s - m, s - m], outline=(34, 197, 94, 200), width=int(s * 0.07))
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.04))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.ellipse([m, m, s - m, s - m], outline=(34, 197, 94, 255), width=int(s * 0.045))
    b = int(s * 0.26)
    for start in (10, 130, 250):
        d.pieslice([b, b, s - b, s - b], start, start + 62, fill=(134, 239, 172, 255))
    h = int(s * 0.43)
    d.ellipse([h, h, s - h, s - h], fill=(17, 20, 27, 255), outline=(34, 197, 94, 255),
              width=max(2, int(s * 0.02)))
    return img


def ensure_icon_file():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    img = make_icon_image(256)
    img.save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return img


# ================================================================ finding the wheel and shade box
# All of this works on a picture of the ASRRGBLED window. Coordinates are window pixels.

def _hsv(img):
    import numpy as np
    a = np.asarray(img.convert("RGB").convert("HSV")).astype(np.int16)
    return a[..., 0], a[..., 1], a[..., 2]


def _hue_dist(h, target):
    import numpy as np
    d = np.abs(h - int(target)) % 256
    return np.minimum(d, 256 - d)


def hue_of_rgb(r, g, b):
    import colorsys
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 256


def detect_wheel(img, exclude=None):
    """Find a hue wheel (filled or ring). Returns dict(cx, cy, r) or None.

    Opposite hues on a wheel sit opposite each other, so the midpoint between the
    centroid of hue h and the centroid of hue h+180 is the wheel's center. Taking the
    median over many hue pairs, then zooming in, ignores other colorful bits.
    exclude: (x0, y0, x1, y1) box to ignore (the shade box)."""
    import numpy as np
    H, S, V = _hsv(img)
    h, w = H.shape
    sat = (S > 130) & (V > 130)
    sat[:, : int(w * 0.25)] = False          # the wheel is on the right side
    if exclude:
        x0, y0, x1, y1 = exclude
        sat[max(y0 - 3, 0):y1 + 4, max(x0 - 3, 0):x1 + 4] = False
    ys_all, xs_all = np.nonzero(sat)
    if len(xs_all) < 300:
        return None
    nb = 12
    bins_all = (H[ys_all, xs_all].astype(int) * nb) // 256

    cx = cy = r = None
    xs, ys, bins = xs_all, ys_all, bins_all
    for _ in range(3):  # estimate, then zoom in around the wheel and re-estimate
        cents = []
        for b in range(nb):
            m = bins == b
            if m.sum() < 15:
                return None
            cents.append((np.median(xs[m]), np.median(ys[m]), xs[m], ys[m]))
        mids = np.array([((cents[b][0] + cents[b + nb // 2][0]) / 2,
                          (cents[b][1] + cents[b + nb // 2][1]) / 2) for b in range(nb // 2)])
        cx, cy = float(np.median(mids[:, 0])), float(np.median(mids[:, 1]))
        r_mid = float(np.median([np.median(np.hypot(c[2] - cx, c[3] - cy)) for c in cents]))
        keep = np.hypot(xs_all - cx, ys_all - cy) < r_mid * 1.6
        xs, ys, bins = xs_all[keep], ys_all[keep], bins_all[keep]
        if len(xs) < 200:
            return None

    # Consistency check: hue bins must go around the center in order
    angs = [math.atan2(-(c[1] - cy), c[0] - cx) for c in cents]
    steps = [((angs[(i + 1) % nb] - angs[i]) + math.pi) % (2 * math.pi) - math.pi for i in range(nb)]
    pos = sum(1 for d in steps if 0.15 < d < 1.2)
    neg = sum(1 for d in steps if -1.2 < d < -0.15)
    if max(pos, neg) < nb - 3:
        return None

    r = float(np.median([np.percentile(np.hypot(c[2] - cx, c[3] - cy), 97) for c in cents]))
    if r < 25:
        return None
    return {"cx": cx, "cy": cy, "r": r, "clockwise": neg > pos}


def detect_all(img):
    """Find the shade box and the wheel, each helping find the other."""
    shade = detect_shade_box(img)
    wheel = detect_wheel(img, exclude=shade["box"] if shade else None)
    if wheel and not shade:
        shade = detect_shade_box(img, wheel)
    return wheel, shade


def wheel_point_for_hue(img, wheel, target_hue):
    """Pixel on the wheel whose color is closest to target_hue (0-255 scale)."""
    import numpy as np
    H, S, V = _hsv(img)
    h, w = H.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.hypot(xx - wheel["cx"], yy - wheel["cy"])
    base = (dist <= wheel["r"] * 1.02) & (dist >= wheel["r"] * 0.88) & (S > 170) & (V > 170)
    for tol in (3, 5, 8, 12):
        cand = base & (_hue_dist(H, target_hue) <= tol)
        ys, xs = np.nonzero(cand)
        if len(xs) >= 6:
            break
    else:
        return None
    # middle of the matching band: median angle and median distance
    angs = np.arctan2(ys - wheel["cy"], xs - wheel["cx"])
    ma = math.atan2(np.median(np.sin(angs)), np.median(np.cos(angs)))
    md = float(np.median(np.hypot(xs - wheel["cx"], ys - wheel["cy"])))
    px, py = wheel["cx"] + md * math.cos(ma), wheel["cy"] + md * math.sin(ma)
    i = int(np.argmin((xs - px) ** 2 + (ys - py) ** 2))
    return int(xs[i]), int(ys[i])


def _gradient_box(Varr, valid):
    """Find a box whose columns fade from bright (top) to black (bottom)."""
    import numpy as np
    h, w = Varr.shape
    segs = []
    for x in range(0, w, 2):
        col = Varr[:, x].astype(int)
        d = np.diff(col)
        good = (d <= 3) & (d >= -40) & valid[1:, x] & valid[:-1, x]
        # runs of "good" steps
        idx = np.flatnonzero(np.diff(np.concatenate(([0], good.astype(int), [0]))))
        for s, e in zip(idx[0::2], idx[1::2]):
            if e - s < 40:
                continue
            seg = d[s:e]
            # trim flat parts at both ends
            nz = np.flatnonzero(seg < 0)
            if len(nz) < 20:
                continue
            s2, e2 = s + nz[0], s + nz[-1] + 1
            if e2 - s2 < 40:
                continue
            seg = d[s2:e2]
            if (seg < 0).mean() < 0.5:
                continue
            if col[s2] < 140 or col[e2] > 40:
                continue
            segs.append((x, s2, e2))
    if not segs:
        return None
    segs.sort()
    groups, cur = [], [segs[0]]
    for sg in segs[1:]:
        p = cur[-1]
        if sg[0] - p[0] <= 4 and abs(sg[1] - cur[0][1]) <= 8 and abs(sg[2] - cur[0][2]) <= 8:
            cur.append(sg)
        elif sg[0] == p[0]:
            continue
        else:
            groups.append(cur)
            cur = [sg]
    groups.append(cur)
    best = max(groups, key=len)
    if best[-1][0] - best[0][0] < 10:
        return None
    tops = sorted(g[1] for g in best)
    bots = sorted(g[2] for g in best)
    return best[0][0], tops[len(tops) // 2], best[-1][0], bots[len(bots) // 2]


def detect_shade_box(img, wheel=None):
    """Find the shade box (fades to black). Returns dict with the box,
    a black point, and a "full color" point, or None. Any orientation works."""
    import numpy as np
    H, S, V = _hsv(img)
    h, w = V.shape
    valid = np.ones_like(V, dtype=bool)
    valid[:, : int(w * 0.25)] = False
    if wheel:
        yy, xx = np.mgrid[0:h, 0:w]
        valid &= np.hypot(xx - wheel["cx"], yy - wheel["cy"]) > wheel["r"] * 1.08

    # (array, valid, map back to window x,y)
    views = [
        (V, valid, lambda x, y: (x, y)),                                   # black at bottom
        (V[::-1], valid[::-1], lambda x, y: (x, h - 1 - y)),               # black at top
        (V.T, valid.T, lambda x, y: (y, x)),                               # black at right
        (V.T[::-1], valid.T[::-1], lambda x, y: (w - 1 - y, x)),           # black at left
    ]
    best = None
    for arr, val, back in views:
        box = _gradient_box(arr, val)
        if not box:
            continue
        x0, top, x1, bot = box
        size = (x1 - x0) * (bot - top)
        if best is None or size > best[0]:
            best = (size, box, back)
    if not best:
        return None
    _, (x0, top, x1, bot), back = best
    return _shade_from_box(S, (x0, top, x1, bot), back)


def _shade_from_box(S, view_box, back, force=None):
    """Work out how the box maps colors, and return spots for any color.
    Two styles exist:
      HSV  - top edge goes white -> full color, bottom is black
      HSL  - top is white, middle of the color edge is full color, bottom is black (ASRock)"""
    h, w = S.shape
    x0, top, x1, bot = view_box

    def sat(px, py):
        return int(S[min(max(py, 0), h - 1), min(max(px, 0), w - 1)])

    best = (0, None, None)   # (saturation, which end, y)
    for y in range(top + 3, bot - 2, 2):
        for end, x in (("near", x0 + 3), ("far", x1 - 3)):
            sv = sat(*back(x, y))
            if sv > best[0]:
                best = (sv, end, y)
    has_sat = best[0] >= 60
    sat_far = best[1] == "far"
    peak = (best[2] - top) / max(bot - top, 1) if has_sat else 0.0
    style = "hsl" if peak > 0.2 else "hsv"
    if force:                       # known layout: ASRock's box, color edge on the right
        style, has_sat, sat_far = force, True, True
    xs_, xe_ = (x0 + 3, x1 - 3)

    def spot(fx, fy):
        x = xs_ + fx * (xe_ - xs_)
        y = top + 3 + fy * (bot - top - 6)
        px, py = back(int(round(x)), int(round(y)))
        return int(px), int(py)

    def point_for_rgb(r, g, b):
        import colorsys
        rr, gg, bb = r / 255, g / 255, b / 255
        if style == "hsl":
            _, l, sa = colorsys.rgb_to_hls(rr, gg, bb)
            fy = 1 - l
        else:
            _, sa, v = colorsys.rgb_to_hsv(rr, gg, bb)
            fy = 1 - v
        if not has_sat:
            fx = 0.5
        else:
            fx = sa if sat_far else 1 - sa
        return spot(fx, fy)

    corners = [back(x0, top), back(x1, top), back(x0, bot), back(x1, bot)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    full = spot(1.0 if sat_far else 0.0, 0.5 if style == "hsl" else 0.0) if has_sat else spot(0.5, 0.0)
    black = spot(0.0 if sat_far else 1.0, 1.0) if has_sat else spot(0.5, 1.0)
    return {"box": (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), "black": black,
            "bright": full, "point_for_rgb": point_for_rgb, "style": style}


# ---- ASRock Polychrome Sync's own layout (measured from a screenshot of it), used as a last resort
KNOWN_LAYOUT = {
    "size": (939, 609),
    "header": (235, 392),            # Addressable RGB LED icon
    "apply": (340, 497),             # Apply All check mark
    "wheel": (659.8, 233.8, 128.5),  # rainbow ring center and outer radius
    "square": (596, 171, 710, 284),  # shade box
    "sliders": (848, 870, 891, 108, 352),  # R, G, B slider x, then top (255) and bottom (0) y
    "toggle_rel": (-0.627, 1.309),         # on/off switch, relative to the wheel (in wheel radii)
}


def layout_fits(size):
    w, h = size
    kw, kh = KNOWN_LAYOUT["size"]
    return abs((w / h) - (kw / kh)) < 0.08 and 0.6 < w / kw < 2.6


def layout_point(size, key):
    w, h = size
    kw, kh = KNOWN_LAYOUT["size"]
    x, y = KNOWN_LAYOUT[key][:2]
    return int(x * w / kw), int(y * h / kh)


def layout_wheel_and_shade(img):
    """Wheel and shade box from the known layout, scaled to this window."""
    w, h = img.size
    kw, kh = KNOWN_LAYOUT["size"]
    sx, sy = w / kw, h / kh
    cx, cy, r = KNOWN_LAYOUT["wheel"]
    wheel = {"cx": cx * sx, "cy": cy * sy, "r": r * (sx + sy) / 2, "clockwise": False}
    x0, y0, x1, y1 = KNOWN_LAYOUT["square"]
    x0, y0, x1, y1 = int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)
    shade = _shade_from_box(_hsv(img)[1], (x0, y0, x1, y1), lambda x, y: (x, y), force="hsl")
    return wheel, shade


def hue_at(img, pt):
    H, S, V = _hsv(img)
    x, y = pt
    return int(H[y, x]), int(S[y, x]), int(V[y, x])


def draw_debug(img, wheel, shade, picks, path):
    from PIL import ImageDraw
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    if wheel:
        r = wheel["r"]
        d.ellipse([wheel["cx"] - r, wheel["cy"] - r, wheel["cx"] + r, wheel["cy"] + r], outline=(255, 0, 255), width=2)
    if shade:
        d.rectangle(shade["box"], outline=(0, 255, 255), width=2)
    for (x, y), col in picks:
        d.ellipse([x - 6, y - 6, x + 6, y + 6], outline=col, width=3)
    im.save(path)


# ---- the three R / G / B sliders to the right of the wheel (exact colors)

def detect_sliders(img, wheel=None):
    """Find the red, green and blue slider tracks. Returns dict(x=[xr, xg, xb], top, bottom) or None."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    x_start = int(wheel["cx"] + wheel["r"] * 1.05) if wheel else int(w * 0.6)
    y_lo = int(wheel["cy"] - wheel["r"] * 1.4) if wheel else 0
    y_hi = int(wheel["cy"] + wheel["r"] * 1.4) if wheel else h
    x_start, y_lo, y_hi = max(0, x_start), max(0, y_lo), min(h, y_hi)
    region = a[y_lo:y_hi, x_start:]
    if region.size == 0:
        return None
    xs, tops, bots = [], [], []
    for ch in range(3):
        others = [o for o in range(3) if o != ch]
        m = (region[..., ch] > 110) & (region[..., others[0]] < 90) & (region[..., others[1]] < 90)
        counts = m.sum(axis=0)
        col = int(np.argmax(counts))
        if counts[col] < 40:
            return None
        rows = np.flatnonzero(m[:, max(col - 1, 0):col + 2].any(axis=1))
        xs.append(x_start + col)
        tops.append(y_lo + int(rows.min()))
        bots.append(y_lo + int(rows.max()))
    gaps = xs[1] - xs[0], xs[2] - xs[1]
    if not (3 <= gaps[0] <= 80 and 3 <= gaps[1] <= 80 and abs(gaps[0] - gaps[1]) <= 12):
        return None
    return {"x": xs, "top": int(np.median(tops)), "bottom": int(np.median(bots))}


def layout_sliders(size):
    w, h = size
    kw, kh = KNOWN_LAYOUT["size"]
    xr, xg, xb, top, bot = KNOWN_LAYOUT["sliders"]
    sx, sy = w / kw, h / kh
    return {"x": [int(xr * sx), int(xg * sx), int(xb * sx)], "top": int(top * sy), "bottom": int(bot * sy)}


def find_knob(img, x, top, bottom):
    """Y of the round white knob on a slider track, or None."""
    import numpy as np
    H, S, V = _hsv(img)
    h, w = V.shape
    pad = max(8, (bottom - top) // 12)
    y0, y1 = max(0, top - pad), min(h, bottom + pad)
    half = max(7, (bottom - top) // 25)           # knob is wider than the track
    x0, x1 = max(0, x - half), min(w, x + half + 1)
    m = (S[y0:y1, x0:x1] < 50) & (V[y0:y1, x0:x1] > 200)
    per_row = m.sum(axis=1)
    rows = np.flatnonzero(per_row >= 3)
    if len(rows) < 3:
        return None
    # the knob is the bunch of rows with the most white pixels (glare is thin)
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 3) + 1)
    best = max(groups, key=lambda g: int(per_row[g].sum()))
    return y0 + int(np.median(best))


# ---- the on/off switch under the wheel (cyan when on) and the color the shade box shows

def _cyan_mask(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r < 130) & (g > 150) & (b > 150) & (abs(g - b) < 80)


def detect_toggle(img, wheel):
    """Center of the cyan on/off switch just below the wheel (only findable while it's on)."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    cx, cy, r = wheel["cx"], wheel["cy"], wheel["r"]
    # the on/off switch sits ~1.3 wheel-radii below the center; "Addressable RGSwap" (also cyan)
    # is ~2.2 below, so stop well above it
    y0, y1 = int(max(0, cy + r * 1.05)), int(min(h, cy + r * 1.65))
    x0, x1 = int(max(0, cx - r * 1.5)), int(min(w, cx + r * 1.0))
    if y1 <= y0 or x1 <= x0:
        return None
    m = _cyan_mask(a[y0:y1, x0:x1])
    rows = np.flatnonzero(m.sum(axis=1) >= 6)
    if len(rows) < 4:
        return None
    first = np.split(rows, np.flatnonzero(np.diff(rows) > 3) + 1)[0]      # topmost switch
    sub = m[first[0]:first[-1] + 1]
    cols = np.flatnonzero(sub.sum(axis=0) >= 2)
    if len(cols) < 8:
        return None
    left = np.split(cols, np.flatnonzero(np.diff(cols) > 3) + 1)[0]
    return int(x0 + (left[0] + left[-1]) / 2), int(y0 + (first[0] + first[-1]) / 2)


def toggle_is_on(img, pt):
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    x, y = pt
    patch = a[max(0, y - 6):y + 7, max(0, x - 12):x + 13]
    return patch.size > 0 and _cyan_mask(patch).mean() > 0.25


def box_hue(img, shade):
    """The hue the shade box is showing right now (= the color picked on the ring)."""
    import numpy as np
    H, S, V = _hsv(img)
    x0, y0, x1, y1 = shade["box"]
    hh, ss, vv = H[y0:y1, x0:x1], S[y0:y1, x0:x1], V[y0:y1, x0:x1]
    m = (ss > 110) & (vv > 60)
    if m.sum() < 20:
        return None
    ang = hh[m].astype(float) / 256 * 2 * math.pi
    return (math.atan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2 * math.pi) * 256) % 256


# ================================================================ reading text on the window (OCR)

def _ocr_worker(img, out):
    try:
        import asyncio
        from PIL import Image
        try:
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
            from winrt.windows.storage.streams import DataWriter
        except ImportError:  # older package name for the same thing
            from winsdk.windows.media.ocr import OcrEngine
            from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
            from winsdk.windows.storage.streams import DataWriter
        scale = 2   # bigger text reads more accurately
        im = img.convert("RGBA").resize((img.width * scale, img.height * scale))
        r, g, b, a = im.split()
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        dw = DataWriter()
        try:
            dw.write_bytes(data)
        except TypeError:
            dw.write_bytes(list(data))
        sb = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, im.width, im.height)
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            raise RuntimeError("no OCR language installed")

        async def go():
            return await eng.recognize_async(sb)
        res = asyncio.run(go())
        lines = []
        for line in res.lines:
            rects = [w.bounding_rect for w in line.words]
            if not rects:
                continue
            x0 = min(rc.x for rc in rects) / scale
            y0 = min(rc.y for rc in rects) / scale
            x1 = max(rc.x + rc.width for rc in rects) / scale
            y1 = max(rc.y + rc.height for rc in rects) / scale
            words = [(w.text, (w.bounding_rect.x / scale, w.bounding_rect.y / scale,
                               (w.bounding_rect.x + w.bounding_rect.width) / scale,
                               (w.bounding_rect.y + w.bounding_rect.height) / scale)) for w in line.words]
            lines.append({"text": line.text, "box": (x0, y0, x1, y1), "words": words})
        out["lines"] = lines
    except Exception as e:
        out["error"] = str(e)


def ocr_lines(img):
    """Text on the picture, with where each line is. Runs in its own thread
    because Windows' text reader needs a clean setup."""
    out = {}
    t = threading.Thread(target=_ocr_worker, args=(img, out), daemon=True)
    t.start()
    t.join(20)
    if "error" in out:
        raise RuntimeError(f"Text reader failed: {out['error']}")
    return out.get("lines", [])


def ocr_find(lines, wanted):
    """Center of the text line matching `wanted` (exact match first, then partial), or None."""
    w = norm(wanted)
    if not w:
        return None
    exact = [ln for ln in lines if norm(ln["text"]) == w]
    part = [ln for ln in lines if w in norm(ln["text"])]
    for ln in exact + part:
        x0, y0, x1, y1 = ln["box"]
        return int((x0 + x1) / 2), int((y0 + y1) / 2)
    return None


# ================================================================ window helpers (Windows)

class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


OFF_X, OFF_Y = -12000, -12000
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
HWND_TOPMOST, HWND_NOTOPMOST, HWND_BOTTOM = -1, -2, 1


def get_placement(hwnd):
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    return wp


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


ORIGINAL_EX = {}   # window -> its style before we made it see-through (to put it back later)


def _set_alpha(hwnd, alpha):
    ex = user32.GetWindowLongW(hwnd, -20)                 # GWL_EXSTYLE
    ORIGINAL_EX.setdefault(hwnd, ex)
    user32.SetWindowLongW(hwnd, -20, ex | 0x00080000)      # WS_EX_LAYERED
    return bool(user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 0x2))  # LWA_ALPHA


def _on_a_monitor(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return bool(user32.MonitorFromRect(ctypes.byref(r), 0))   # MONITOR_DEFAULTTONULL


def hide_window(hwnd):
    """Keep ASRRGBLED where Windows wants it, but invisible (1/255 opacity) and behind
    everything. It still draws itself, so the app can read it and press its buttons.
    Returns what's needed to put it back exactly how it was."""
    state = {"ex": ORIGINAL_EX.get(hwnd, user32.GetWindowLongW(hwnd, -20)), "wp": get_placement(hwnd)}
    _set_alpha(hwnd, 1)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)          # SW_SHOWNOACTIVATE: un-minimize, no focus
    if not _on_a_monitor(hwnd):
        work = wintypes.RECT()
        user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)
        user32.SetWindowPos(hwnd, 0, work.left, work.top, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, 0x0002 | SWP_NOSIZE | SWP_NOACTIVATE)
    return state


def unhide_window(hwnd, state, minimize=False):
    user32.SetWindowLongW(hwnd, -20, state["ex"])
    user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0400)
    wp = state["wp"]
    if minimize:
        wp.showCmd = 7  # SW_SHOWMINNOACTIVE
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))


def capture_window(hwnd):
    """Picture of the window, read straight from the window itself (nothing shows on screen)."""
    from PIL import Image
    import numpy as np
    l, t, r, b = window_rect(hwnd)
    w, h = max(r - l, 1), max(b - t, 1)
    img = None
    for flag in (2, 0):  # PW_RENDERFULLCONTENT, then the classic way
        sdc = user32.GetDC(0)
        mdc = gdi32.CreateCompatibleDC(sdc)
        bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
        old = gdi32.SelectObject(mdc, bmp)
        user32.PrintWindow(hwnd, mdc, flag)
        bmi = BITMAPINFOHEADER(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(0, sdc)
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
        if np.asarray(img).mean() > 3:
            break
    return img


def post_click(hwnd, wx, wy):
    """Click inside the window with window messages. The real mouse never moves."""
    l, t, _, _ = window_rect(hwnd)
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    pt = wintypes.POINT(l + wx - origin.x, t + wy - origin.y)
    target = hwnd
    for _ in range(10):  # find the deepest child window under the point
        child = user32.ChildWindowFromPointEx(target, pt, 0x0001 | 0x0002 | 0x0004)
        if not child or child == target:
            break
        user32.MapWindowPoints(target, child, ctypes.byref(pt), 1)
        target = child
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    for msg, wp in ((0x0200, 0), (0x0201, 1), (0x0200, 1), (0x0202, 0)):
        user32.PostMessageW(target, msg, wp, lp)
        time.sleep(0.03)
    time.sleep(0.25)


def real_clicks(hwnd, points):
    """Backup: real clicks on the window while it stays invisible. Only the mouse
    pointer blinks for a moment, then goes back where it was."""
    cur = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cur))
    flags = 0x0002 | SWP_NOSIZE | SWP_NOACTIVATE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    time.sleep(0.12)
    try:
        l, t, _, _ = window_rect(hwnd)
        for wx, wy in points:
            user32.SetCursorPos(l + wx, t + wy)
            time.sleep(0.04)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.04)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.25)
    finally:
        user32.SetCursorPos(cur.x, cur.y)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, flags)


def _lparam(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def post_drag(hwnd, start, end, steps=8):
    """Drag inside the window with window messages (press at start, move, let go at end).
    The real mouse never moves."""
    l, t, _, _ = window_rect(hwnd)
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    pt = wintypes.POINT(l + start[0] - origin.x, t + start[1] - origin.y)
    target = hwnd
    for _ in range(10):
        child = user32.ChildWindowFromPointEx(target, pt, 0x0001 | 0x0002 | 0x0004)
        if not child or child == target:
            break
        user32.MapWindowPoints(target, child, ctypes.byref(pt), 1)
        target = child
    # offset from window pixels to the target control's own pixels
    dx, dy = pt.x - start[0], pt.y - start[1]
    user32.PostMessageW(target, 0x0200, 0, _lparam(pt.x, pt.y))
    user32.PostMessageW(target, 0x0201, 1, _lparam(pt.x, pt.y))      # button down
    time.sleep(0.05)
    for i in range(1, steps + 1):
        x = start[0] + (end[0] - start[0]) * i // steps + dx
        y = start[1] + (end[1] - start[1]) * i // steps + dy
        user32.PostMessageW(target, 0x0200, 1, _lparam(x, y))      # move with button held
        time.sleep(0.02)
    user32.PostMessageW(target, 0x0202, 0, _lparam(end[0] + dx, end[1] + dy))  # button up
    time.sleep(0.25)


def real_drag(hwnd, start, end, steps=8):
    """Backup: a real mouse drag on the invisible window. The pointer jumps back after."""
    cur = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cur))
    flags = 0x0002 | SWP_NOSIZE | SWP_NOACTIVATE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    time.sleep(0.1)
    try:
        l, t, _, _ = window_rect(hwnd)
        user32.SetCursorPos(l + start[0], t + start[1])
        time.sleep(0.04)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        for i in range(1, steps + 1):
            user32.SetCursorPos(l + start[0] + (end[0] - start[0]) * i // steps,
                                t + start[1] + (end[1] - start[1]) * i // steps)
            time.sleep(0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.2)
    finally:
        user32.SetCursorPos(cur.x, cur.y)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, flags)


# ================================================================ ASRRGBLED control

def find_asr_exe():
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramFiles", r"C:\Program Files")]
    patterns = ["ASRock Utility/**/ASRRGBLED*.exe", "ASRock Utility/**/*Polychrome*.exe",
                "ASRock*/**/ASRRGBLED*.exe", "ASRock*/**/*RGB*LED*.exe"]
    for root in roots:
        for pat in patterns:
            hits = glob.glob(os.path.join(root, pat), recursive=True)
            hits = [h for h in hits if "unins" not in os.path.basename(h).lower()]
            if hits:
                return hits[0]
    return ""


def asr_pids(cfg):
    import psutil
    target = os.path.basename(cfg.get("asr_path", "")).lower()
    pids = []
    for p in psutil.process_iter(["name"]):
        try:
            n = (p.info["name"] or "").lower()
            if n and (n == target or "asrrgbled" in n or "polychrome" in n):
                pids.append(p.pid)
        except Exception:
            pass
    return pids


def _class_of(hwnd):
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    return cls.value


def _title_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def asr_windows(cfg):
    """Every visible ASRRGBLED window: [(hwnd, class, title, (w, h), minimized)]."""
    pids = set(asr_pids(cfg))
    found = []
    if not pids:
        return found

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            l, t, r, b = window_rect(hwnd)
            found.append((hwnd, _class_of(hwnd), _title_of(hwnd), (r - l, b - t), bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return found


def find_asr_hwnd(cfg):
    """ASRRGBLED's main window. Picks the one shaped like Polychrome's window first,
    then the biggest. Splash screens and popups are skipped."""
    best = None
    for hwnd, cls, title, (w, h), iconic in asr_windows(cfg):
        if cls in ("#32770", "TMessageForm", "TApplication", "tooltips_class32"):
            continue
        owner = user32.GetWindow(hwnd, 4)       # GW_OWNER
        # Delphi apps (like ASRock's) often hang the main window off a hidden TApplication window
        if owner and _class_of(owner) != "TApplication":
            continue
        if not iconic and (w * h < 200 * 150 or (not layout_fits((w, h)) and w < 700 and h < 500)):
            continue                            # too small: a popup, not the main window
        score = (iconic or layout_fits((w, h)), w * h, bool(title))
        if best is None or score > best[0]:
            best = (score, hwnd)
    return best[1] if best else None


def describe_window(hwnd):
    if not hwnd:
        return "no window"
    l, t, r, b = window_rect(hwnd)
    return f"{_class_of(hwnd)!r} \"{_title_of(hwnd)}\" {r - l}x{b - t}"


def launch_asr(path):
    """Start ASRRGBLED normally but without stealing focus. (Starting it minimized can
    make ASRock's app crash while it loads.)"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4  # SW_SHOWNOACTIVATE
    # CREATE_NO_WINDOW: ASRRGBLED starts as a command-prompt program; this stops that
    # black window from ever appearing (it does nothing to normal windows)
    return subprocess.Popen([path], cwd=os.path.dirname(path), startupinfo=si,
                            creationflags=NO_WINDOW).pid


class WindowHider:
    """While ASRRGBLED is being used, makes every window it opens (the main window, the
    Realtek popup, anything else) see-through the instant Windows creates it, with the
    open animation turned off, so nothing flashes on screen."""

    def __init__(self, cfg, consoles_only=False):
        self.cfg = cfg
        self.consoles_only = consoles_only
        self.thread_id = None
        self.names = {}
        self.ready = threading.Event()
        self._proc = None

    def _is_asr(self, pid):
        if pid not in self.names:
            try:
                import psutil
                n = psutil.Process(pid).name().lower()
            except Exception:
                n = ""
            target = os.path.basename(self.cfg.get("asr_path", "")).lower()
            hit = bool(n) and (n == target or "asrrgbled" in n or "polychrome" in n or n.startswith("asr"))
            if not hit and n:
                try:                                   # anything ASRRGBLED itself started
                    import psutil
                    for parent in psutil.Process(pid).parents()[:3]:
                        pn = parent.name().lower()
                        if pn == target or "asrrgbled" in pn or "polychrome" in pn:
                            hit = True
                            break
                except Exception:
                    pass
            self.names[pid] = hit
        return self.names[pid]

    @staticmethod
    def _asr_console(hwnd):
        """A command-prompt window ASRRGBLED (or its helpers) opened. Its title is the
        program's path, which has "ASRock" or "Asr" in it."""
        if _class_of(hwnd) not in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            return False
        t = _title_of(hwnd).lower()
        return any(k in t for k in ("asrock", "asrrgb", "polychrome", "\\asr", "rgbled", "asrisp"))

    @staticmethod
    def _hide_console(hwnd):
        """Command-prompt windows ignore see-through, so hide them outright. The program
        inside keeps running. (Clicking into one would also pause it.)"""
        user32.ShowWindow(hwnd, 0)                          # SW_HIDE

    def _poll_consoles(self):
        """Backup for the event hook: sweep for ASRock command prompts many times a second."""
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and self._asr_console(hwnd):
                found.append(hwnd)
            return True
        while self.thread_id or not self.ready.is_set():
            found.clear()
            try:
                user32.EnumWindows(cb, 0)
                for h in found:
                    self._hide_console(h)
            except Exception:
                pass
            time.sleep(0.03)

    def _on_event(self, _hook, _event, hwnd, id_object, id_child, _thread, _time):
        try:
            if id_object != 0 or id_child != 0 or not hwnd:
                return
            if user32.GetAncestor(hwnd, 2) != hwnd:      # GA_ROOT: only top-level windows
                return
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self._asr_console(hwnd):
                self._hide_console(hwnd)
                return
            if self.consoles_only:
                return
            if (self._is_asr(pid.value)
                    or (_class_of(hwnd) == "#32770" and "realtek" in _title_of(hwnd).lower())):
                off = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(off), 4)  # no open animation
                _set_alpha(hwnd, 1)                       # (remembers its original style first)
                ex = user32.GetWindowLongW(hwnd, -20)
                user32.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)  # no taskbar button popping up
        except Exception:
            pass

    def _run(self):
        proc_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                       wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        self._proc = proc_type(self._on_event)
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc_type,
                                           wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        # EVENT_OBJECT_CREATE (0x8000) .. EVENT_OBJECT_SHOW (0x8002), outside the other app
        hook = user32.SetWinEventHook(0x8000, 0x8002, None, self._proc, 0, 0, 0x0000 | 0x0002)
        hook2 = user32.SetWinEventHook(0x800C, 0x800C, None, self._proc, 0, 0, 0x0000 | 0x0002)  # title changes
        self.thread_id = kernel32.GetCurrentThreadId()
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for h in (hook, hook2):
            if h:
                user32.UnhookWinEvent(h)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(2)
        threading.Thread(target=self._poll_consoles, daemon=True).start()
        return self

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)   # WM_QUIT
            self.thread_id = None


class AsrCrashed(RuntimeError):
    pass


def _window_texts(hwnd):
    texts = []
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    texts.append(buf.value)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(ch, _):
        b = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(ch, b, 512)
        texts.append(b.value)
        return True
    user32.EnumChildWindows(hwnd, cb, 0)
    return " ".join(texts).lower()


def with_time_limit(fn, seconds, default=None):
    """Run fn in its own thread; give up after `seconds` (UI Automation can freeze on
    a window that's busy or blocked by a popup)."""
    out = {"v": default}

    def run():
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            out["v"] = fn()
        except Exception:
            pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return out["v"]


def _uia_texts(hwnd):
    def read():
        from pywinauto import Desktop
        w = Desktop(backend="uia").window(handle=hwnd)
        return " ".join((e.element_info.name or "") for e in w.descendants()).lower()
    return with_time_limit(read, 3, "") or ""


def asr_has_any_popup(pids):
    """Any small message box from ASRRGBLED (used at startup, where any leftover popup means trouble)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value in ("#32770", "TMessageForm"):
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return bool(found)


DIALOG_CLASSES = ("#32770", "TMessageForm")


def _is_crash_text(t):
    """True for ASRRGBLED's crash popups. The Realtek audio notice it shows at
    startup is NOT a crash, even if it says "error" or "fail"."""
    if any(k in t for k in ("violation", "exception")):
        return True
    return any(k in t for k in ("error", "fail")) and not any(k in t for k in ("realtek", "audio"))


def find_crash_dialog(pids):
    """ASRRGBLED's own error popup ("Access violation ..."), if it's showing."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and _class_of(hwnd) in DIALOG_CLASSES:
            if _is_crash_text(_window_texts(hwnd) + " " + _uia_texts(hwnd)):
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def find_popups(cfg):
    """Popups that block ASRRGBLED (like its Realtek audio notice): its message boxes,
    small windows of its own that aren't the main window, and any message box anywhere
    that mentions Realtek."""
    pids = set(asr_pids(cfg))
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = _class_of(hwnd)
        l, t, r, b = window_rect(hwnd)
        w, h = r - l, b - t
        if pid.value in pids:
            if cls in ("TApplication", "tooltips_class32") or w * h < 60 * 40:
                return True
            if cls in DIALOG_CLASSES or (not layout_fits((w, h)) and w < 700 and h < 500):
                found.append(hwnd)
        elif cls == "#32770" and "realtek" in (_title_of(hwnd) + " " + _window_texts(hwnd)).lower():
            found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found


OK_WORDS = ("ok", "yes", "confirm", "continue", "close", "accept", "agree", "next", "確定", "确定", "확인")


def press_popup_button(hwnd):
    """Press the OK-type button on a popup. Falls back to Enter, then closing it."""
    def by_button():
        from pywinauto import Desktop
        btns = [b for b in Desktop(backend="uia").window(handle=hwnd).descendants()
                if (b.element_info.control_type or "") == "Button"]
        named = [b for b in btns if norm(b.element_info.name) in OK_WORDS]
        for b in named or (btns if len(btns) == 1 else []):
            for press in (lambda: b.invoke(), lambda: b.click(),
                          lambda: b.iface_legacy_iaccessible.DoDefaultAction()):
                try:
                    press()
                    return True
                except Exception:
                    pass
        return False
    if with_time_limit(by_button, 4, False):
        return True
    # classic message boxes: Enter presses the default button
    user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)   # WM_KEYDOWN VK_RETURN
    user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)
    time.sleep(0.4)
    if user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return True


def dismiss_popups(cfg, log=None):
    """Click through ASRRGBLED's startup popups. Returns how many it closed."""
    closed = 0
    for hwnd in find_popups(cfg):
        text = (_title_of(hwnd) + " " + _window_texts(hwnd) + " " + _uia_texts(hwnd)).lower()
        if _is_crash_text(text):
            continue                               # crashes are handled by check()
        _set_alpha(hwnd, 1)                         # keep it out of sight
        press_popup_button(hwnd)
        closed += 1
        if log:
            what = "Realtek audio popup" if "realtek" in text or "audio" in text else \
                f"popup \"{_title_of(hwnd) or text.strip()[:40]}\""
            log(f"Clicked through ASRRGBLED's {what}.")
    if closed:
        time.sleep(0.6)
    return closed


def kill_asr(cfg):
    import psutil
    for p in asr_pids(cfg):
        try:
            psutil.Process(p).kill()
        except Exception:
            pass
    time.sleep(0.5)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class AsrSession:
    """Opens ASRRGBLED invisibly, does its work, and closes it (or puts it back)."""

    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.hwnd = None
        self.win = None
        self.we_opened = False
        self.state = None
        self._els = None

    def check(self):
        """If ASRRGBLED crashed, clear its error popup, end it, and say so."""
        pids = set(asr_pids(self.cfg))
        if pids and find_crash_dialog(pids):
            kill_asr(self.cfg)
            self.hwnd = None
            raise AsrCrashed("ASRRGBLED crashed.")

    def __enter__(self):
        self.hider = WindowHider(self.cfg).start()
        try:
            return self._open()
        except BaseException:
            self.hider.stop()
            raise

    def _open(self):
        from pywinauto import Desktop
        pids = set(asr_pids(self.cfg))
        if pids:
            dismiss_popups(self.cfg, self.log)     # e.g. the Realtek audio notice left open
            pids = set(asr_pids(self.cfg))
        if pids and (asr_has_any_popup(pids) or not find_asr_hwnd(self.cfg)):
            # an old copy that crashed or is stuck in the background blocks a new one from opening
            self.log("Closing a stuck copy of ASRRGBLED...")
            kill_asr(self.cfg)
            time.sleep(0.8)
        hwnd = find_asr_hwnd(self.cfg)
        if not hwnd:
            path = self.cfg.get("asr_path") or find_asr_exe()
            if not path or not os.path.exists(path):
                raise RuntimeError("Can't find ASRRGBLED. Set its path in Settings.")
            self.cfg["asr_path"] = path
            self.hider.names[launch_asr(path)] = True   # hide its windows from the very first one
            self.we_opened = True
            deadline = time.time() + 40
            while time.time() < deadline:
                dismiss_popups(self.cfg, self.log)   # the main window only shows after this
                hwnd = find_asr_hwnd(self.cfg)
                if hwnd:
                    _set_alpha(hwnd, 1)          # invisible the moment it appears
                    break
                p2 = set(asr_pids(self.cfg))
                if p2 and find_crash_dialog(p2):
                    kill_asr(self.cfg)
                    raise AsrCrashed("ASRRGBLED crashed while opening.")
                time.sleep(0.05)
            if not hwnd:
                import psutil
                names = sorted({psutil.Process(x).name() for x in asr_pids(self.cfg) if psutil.pid_exists(x)})
                kill_asr(self.cfg)
                raise RuntimeError("ASRRGBLED didn't open in time"
                                   + (f" (running: {', '.join(names)})." if names else
                                      f" (nothing started from {os.path.basename(path)})."))
        # The first window can be a loading/splash screen. Wait (up to 20s) for the real
        # one: shaped like Polychrome's window and actually drawing something.
        deadline = time.time() + 20
        while True:
            self.check()
            dismiss_popups(self.cfg, self.log)
            newest = find_asr_hwnd(self.cfg) or hwnd
            if newest != hwnd:
                hwnd = newest
                if self.we_opened:
                    _set_alpha(hwnd, 1)
            if user32.IsWindow(hwnd) and not user32.IsIconic(hwnd):
                l, t, r, b = window_rect(hwnd)
                if layout_fits((r - l, b - t)) and not AsrSession.is_blank(capture_window(hwnd)):
                    break
            elif user32.IsWindow(hwnd) and not self.we_opened:
                break                               # minimized copy we didn't open: hide_window un-minimizes it
            if time.time() > deadline:
                self.log(f"ASRRGBLED window looks odd: {describe_window(hwnd)}")
                break
            time.sleep(0.25)
        self.hwnd = hwnd
        self.win = Desktop(backend="uia").window(handle=hwnd)
        time.sleep(0.8 if self.we_opened else 0.3)
        self.check()
        self.state = hide_window(hwnd)
        time.sleep(0.3)
        return self

    def __exit__(self, exc_type, *exc):
        try:
            return self._finish(exc_type)
        finally:
            time.sleep(0.3)
            self.hider.stop()

    def _finish(self, exc_type):
        if not self.hwnd:
            return False
        if exc_type is not None and exc_type is not AsrCrashed:
            self._save_failure()
        if exc_type is not None and self.we_opened:
            kill_asr(self.cfg)                   # never leave a half-used copy around
        elif self.cfg.get("close_after", False):
            if self.we_opened:
                self._close_for_real()
            elif self.state:
                unhide_window(self.hwnd, self.state)
        # otherwise keep Polychrome running, invisible, so the next change is fast and
        # there's no loading screen / popup at all
        return False

    def _save_failure(self):
        """On a failure, save what the app was looking at (no Scan needed) and log a short summary."""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            img = capture_window(self.hwnd) if user32.IsWindow(self.hwnd) else None
            blank = AsrSession.is_blank(img)
            info = [f"Used: {describe_window(self.hwnd)}  picture {'BLANK' if blank else 'ok'}",
                    f"Fits Polychrome layout: {bool(img) and layout_fits(img.size)}", "", "All ASRRGBLED windows:"]
            for hwnd, cls, title, (w, h), iconic in asr_windows(self.cfg):
                owner = user32.GetWindow(hwnd, 4)
                info.append(f"  {cls!r} \"{title}\" {w}x{h}{' minimized' if iconic else ''}"
                            f" owner={_class_of(owner) if owner else '-'}")
            with open(os.path.join(CONFIG_DIR, "last_fail.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(info))
            if img is not None:
                img.save(os.path.join(CONFIG_DIR, "last_fail.png"))
            self.log(f"Saw {describe_window(self.hwnd)}, picture {'blank' if blank else 'ok'}.")
        except Exception as e:
            self.log(f"(couldn't save failure info: {e})")

    def _close_for_real(self):
        """WM_CLOSE first; if ASRRGBLED just hides to the tray, end it (the color is already saved)."""
        import psutil
        pids = asr_pids(self.cfg)
        user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
        deadline = time.time() + 3
        while time.time() < deadline and any(psutil.pid_exists(p) for p in pids):
            time.sleep(0.1)
        for p in pids:
            try:
                psutil.Process(p).terminate()
            except Exception:
                pass

    # ---------------- finding controls by name (read once per step, which is much faster)
    def elements(self):
        if self._els is None:
            self._els = with_time_limit(lambda: self.win.descendants(), 8, []) or []
        return self._els

    def changed(self):
        self._els = None

    def find_named(self, wanted, exact=False, types=None):
        w = norm(wanted)
        exact_hits, part_hits = [], []
        for el in self.elements():
            ei = el.element_info
            n, t = norm(ei.name), (ei.control_type or "")
            if not n or (types and t not in types):
                continue
            if n == w:
                exact_hits.append(el)
            elif not exact and w in n:
                part_hits.append(el)
        return (exact_hits or part_hits or [None])[0]

    def click_element(self, el):
        """Press a control: by automation first, then by a hidden click on its center."""
        t = el.element_info.control_type or ""
        tries = [lambda: el.invoke(), lambda: el.select(), lambda: el.toggle(),
                 lambda: el.iface_legacy_iaccessible.DoDefaultAction()]
        if t in ("ListItem", "TabItem", "RadioButton", "TreeItem", "DataItem"):
            tries.insert(0, tries.pop(1))
        self.changed()

        def attempt():
            for fn in tries:
                try:
                    fn()
                    return True
                except Exception:
                    pass
            return False
        if with_time_limit(attempt, 5, False):
            return True
        try:
            rc = el.rectangle()
            l, tp, _, _ = window_rect(self.hwnd)
            self.click((rc.left + rc.right) // 2 - l, (rc.top + rc.bottom) // 2 - tp)
            return True
        except Exception:
            return False

    @staticmethod
    def set_text(el, value):
        for fn in (lambda: el.set_edit_text(value), lambda: el.iface_value.SetValue(value),
                   lambda: el.iface_range_value.SetValue(float(value))):
            try:
                fn()
                return True
            except Exception:
                pass
        return False

    # ---------------- clicking at a spot
    def click(self, wx, wy, method=None):
        method = method or self.cfg.get("click_method", "auto")
        if method == "real" and self.cfg.get("allow_visible_clicks", True):
            real_clicks(self.hwnd, [(wx, wy)])
        else:
            post_click(self.hwnd, wx, wy)

    # ---------------- modes, boxes, sliders
    def choose_mode(self, names):
        types = ("RadioButton", "ListItem", "Button", "TabItem", "MenuItem", "DataItem", "Text")
        for name in names:
            el = self.find_named(name, exact=True, types=types)
            if el and self.click_element(el):
                return True
        for combo in self.elements():
            if (combo.element_info.control_type or "") != "ComboBox":
                continue
            try:
                items = combo.texts()
            except Exception:
                items = []
            for name in names:
                for item in items:
                    if norm(item) == norm(name):
                        try:
                            combo.select(item)
                            self.changed()
                            return True
                        except Exception:
                            pass
        return False

    def set_rgb_boxes(self, r, g, b):
        edits = [e for e in self.elements() if (e.element_info.control_type or "") in ("Edit", "Spinner")]

        def by(*keys):
            for e in edits:
                if norm(e.element_info.name) in keys:
                    return e
            return None

        red, grn, blu = by("r", "red"), by("g", "green"), by("b", "blue")
        if red and grn and blu:
            return all(self.set_text(e, str(v)) for e, v in ((red, r), (grn, g), (blu, b)))
        hexv = f"{r:02X}{g:02X}{b:02X}"
        for e in edits:
            try:
                cur = (e.get_value() or "").strip()
            except Exception:
                cur = ""
            if "hex" in norm(e.element_info.name) or cur.startswith("#") or re.fullmatch(r"[0-9A-Fa-f]{6}", cur):
                return self.set_text(e, ("#" + hexv) if cur.startswith("#") else hexv)
        numeric = []
        for e in edits:
            try:
                cur = (e.get_value() or "").strip()
            except Exception:
                cur = ""
            if cur.isdigit() and int(cur) <= 255:
                rc = e.rectangle()
                numeric.append((rc.top // 10, rc.left, e))
        if len(numeric) == 3:
            numeric.sort(key=lambda x: (x[0], x[1]))
            return all(self.set_text(x[2], str(v)) for x, v in zip(numeric, (r, g, b)))
        return False

    def set_brightness(self, value):
        for el in self.elements():
            if (el.element_info.control_type or "") != "Slider":
                continue
            if any(k in norm(el.element_info.name) for k in ("bright", "intens", "level", "dim")):
                return self.set_text(el, str(value))
        return False

    # ---------------- wheel / shade box
    @staticmethod
    def is_blank(img):
        import numpy as np
        return img is None or np.asarray(img).mean() < 4

    def see(self):
        """Look at ASRRGBLED: find the ring and shade box by color, or fall back to its known layout."""
        img = capture_window(self.hwnd)
        wheel, shade = (None, None) if self.is_blank(img) else detect_all(img)
        if wheel and shade:
            return img, wheel, shade, "seen"
        if layout_fits(img.size):
            lw, ls = layout_wheel_and_shade(img)
            return img, wheel or lw, shade or ls, "layout"
        return img, wheel, shade, "seen"

    def ring_point(self, img, wheel, hue256, source):
        pt = None if (source == "layout" and self.is_blank(img)) else wheel_point_for_hue(img, wheel, hue256)
        if pt:
            return pt
        # ASRock's ring: red on the right, colors going counter-clockwise
        ang = math.radians(hue256 / 256 * 360)
        rr = wheel["r"] * 0.94
        return int(wheel["cx"] + rr * math.cos(ang)), int(wheel["cy"] - rr * math.sin(ang))

    def learn_click_method(self, img, wheel, shade):
        """Check whether hidden clicks work by clicking the opposite hue and seeing the shade box change."""
        if self.cfg.get("click_method") in ("post", "real"):
            return self.cfg["click_method"]
        visible_ok = self.cfg.get("allow_visible_clicks", True)
        if self.is_blank(img) or not (wheel and shade):
            return "real" if visible_ok else "post"     # can't check, so use the sure way (not saved)
        before = hue_at(img, shade["bright"])[0]
        probe = wheel_point_for_hue(img, wheel, (before + 128) % 256)
        if not probe:
            return "real" if visible_ok else "post"
        post_click(self.hwnd, *probe)
        after = hue_at(capture_window(self.hwnd), shade["bright"])[0]
        changed = min(abs(after - before), 256 - abs(after - before)) > 30
        method = "post" if changed else ("real" if visible_ok else "post")
        self.cfg["click_method"] = method
        self.log("Hidden clicks work." if method == "post" else "Using quick invisible real clicks.")
        return method

    def drag(self, start, end, method):
        if method == "real" and self.cfg.get("allow_visible_clicks", True):
            real_drag(self.hwnd, start, end)
        else:
            post_drag(self.hwnd, start, end)

    def set_rgb_sliders(self, r, g, b):
        """Set the exact color with Polychrome's R / G / B sliders. Each slider is first pushed
        to the very bottom and very top to learn where 0 and 255 are, then moved to the value."""
        if self.cfg.get("sliders_ok") is False:
            return False
        img = capture_window(self.hwnd)
        if self.is_blank(img):
            return False
        wheel, _ = detect_all(img)
        sl = detect_sliders(img, wheel)
        if not sl and layout_fits(img.size):
            sl = layout_sliders(img.size)
        if not sl:
            self.log("Couldn't find the R/G/B sliders.")
            return False
        span = max(sl["bottom"] - sl["top"], 40)
        # Only hidden drags: real drags move your mouse, so they're never used here.
        for method in ["post"]:
            ok = True
            for x, value in zip(sl["x"], (r, g, b)):
                knob = find_knob(capture_window(self.hwnd), x, sl["top"], sl["bottom"])
                if knob is None:
                    ok = False
                    break
                low, high = sl["bottom"] + span, sl["top"] - span      # well past both ends
                self.drag((x, knob), (x, low), method)
                y0 = find_knob(capture_window(self.hwnd), x, sl["top"], sl["bottom"])
                if y0 is None:
                    ok = False
                    break
                if value == 0:
                    if abs(y0 - sl["bottom"]) > span * 0.15:
                        ok = False                          # not at the bottom: the drag didn't work
                        break
                    continue                                # at 0
                self.drag((x, y0), (x, high), method)       # push to 255
                y255 = find_knob(capture_window(self.hwnd), x, sl["top"], sl["bottom"])
                if y255 is None or y0 - y255 < 20:
                    ok = False                              # the knob didn't move
                    break
                if value < 255:
                    target = int(round(y0 - value / 255 * (y0 - y255)))
                    self.drag((x, y255), (x, target), method)
            if ok:
                return True
        # don't waste time on them again (Scan in Settings turns them back on)
        self.cfg["sliders_ok"] = False
        self.log("The R/G/B sliders don't respond to hidden drags, so the color wheel will be used from now on.")
        return False

    def _ring_click_at_angle(self, wheel, ang, rr, method):
        pt = int(wheel["cx"] + rr * math.cos(ang)), int(wheel["cy"] - rr * math.sin(ang))
        self.click(*pt, method=method)
        return pt

    # ---------------- reading the exact color back from the R/G/B slider knobs
    def _sliders(self, img, wheel):
        sl = detect_sliders(img, wheel) if wheel else None
        if not sl and layout_fits(img.size):
            sl = layout_sliders(img.size)
        return sl

    def read_rgb(self, sl, cal):
        """The color Polychrome is set to right now, read from where the 3 knobs sit."""
        img = capture_window(self.hwnd)
        vals = []
        for x in sl["x"]:
            y = find_knob(img, x, sl["top"], sl["bottom"])
            if y is None:
                return None
            v = (cal["y0"] - y) / max(cal["y0"] - cal["y255"], 1) * 255
            vals.append(int(round(min(255, max(0, v)))))
        return tuple(vals)

    def _debug_picture(self, name, sl=None, shade=None, wheel=None, note=""):
        """Save what the app saw, with what it found marked on it (for troubleshooting)."""
        try:
            from PIL import ImageDraw
            img = capture_window(self.hwnd).convert("RGB")
            d = ImageDraw.Draw(img)
            if wheel:
                r = wheel["r"]
                d.ellipse([wheel["cx"] - r, wheel["cy"] - r, wheel["cx"] + r, wheel["cy"] + r],
                          outline=(255, 0, 255), width=2)
            if shade:
                d.rectangle(shade["box"], outline=(0, 255, 255), width=2)
            if sl:
                for x in sl["x"]:
                    d.line([x, sl["top"], x, sl["bottom"]], fill=(255, 255, 0), width=1)
                    k = find_knob(img, x, sl["top"], sl["bottom"])
                    if k is not None:
                        d.ellipse([x - 8, k - 8, x + 8, k + 8], outline=(255, 128, 0), width=2)
            if note:
                d.text((8, 8), note, fill=(255, 255, 255))
            os.makedirs(CONFIG_DIR, exist_ok=True)
            img.save(os.path.join(CONFIG_DIR, name))
        except Exception:
            pass

    def calibrate_sliders(self, sl, shade, method, wheel=None):
        """Learn where 0 and 255 are on the sliders: pick black, then white, and see where
        the knobs land. Saved, so it only happens once."""
        import numpy as np
        size = list(capture_window(self.hwnd).size)
        cal = self.cfg.get("slider_cal")
        if cal and cal.get("size") == size and len(cal.get("x", [])) == 3 and \
                all(abs(a - b) <= 3 for a, b in zip(cal["x"], sl["x"])):
            return cal
        x0, y0, x1, y1 = shade["box"]
        w, h = x1 - x0, y1 - y0
        # a little inside the corners so the clicks can't miss the box
        spots = {"y0": ((x0 + x1) // 2, y1 - max(3, h // 30)),
                 "y255": (x0 + max(3, w // 30), y0 + max(3, h // 30))}
        got = {}
        for key, pt in spots.items():
            self.click(*pt, method=method)
            time.sleep(0.2)
            img = capture_window(self.hwnd)
            ys = [find_knob(img, x, sl["top"], sl["bottom"]) for x in sl["x"]]
            if None in ys:
                why = "couldn't find the slider knobs"
            elif max(ys) - min(ys) > 14:
                why = f"knobs didn't line up after picking {'black' if key == 'y0' else 'white'} {ys}"
            else:
                got[key] = float(np.median(ys))
                continue
            self.log(f"Color readout: {why}. Saved color_check.png.")
            self._debug_picture("color_check.png", sl, shade, wheel, why)
            return None
        if got["y0"] - got["y255"] < 40:
            why = f"black and white looked the same ({got})"
            self.log(f"Color readout: {why}. Saved color_check.png.")
            self._debug_picture("color_check.png", sl, shade, wheel, why)
            return None
        cal = {"size": size, "x": sl["x"], "y0": got["y0"], "y255": got["y255"]}
        self.cfg["slider_cal"] = cal
        self.log("Learned the R/G/B readout.")
        return cal

    def _ring_click_at_angle(self, wheel, ang, rr, method):
        pt = int(wheel["cx"] + rr * math.cos(ang)), int(wheel["cy"] - rr * math.sin(ang))
        self.click(*pt, method=method)
        return pt

    # ---------------- reading the exact color back from the R/G/B slider knobs
    def _sliders(self, img, wheel):
        sl = detect_sliders(img, wheel) if wheel else None
        if not sl and layout_fits(img.size):
            sl = layout_sliders(img.size)
        return sl

    def read_rgb(self, sl, cal):
        """The color Polychrome is set to right now, read from where the 3 knobs sit."""
        img = capture_window(self.hwnd)
        vals = []
        for x in sl["x"]:
            y = find_knob(img, x, sl["top"], sl["bottom"])
            if y is None:
                return None
            v = (cal["y0"] - y) / max(cal["y0"] - cal["y255"], 1) * 255
            vals.append(int(round(min(255, max(0, v)))))
        return tuple(vals)

    def calibrate_sliders(self, sl, shade, method):
        """Learn where 0 and 255 are on the sliders: pick pure black, then pure white,
        and see where the knobs land. Saved, so it only happens once."""
        import numpy as np
        size = list(capture_window(self.hwnd).size)
        cal = self.cfg.get("slider_cal")
        if cal and cal.get("size") == size and cal.get("x") == sl["x"]:
            return cal
        x0, y0, x1, y1 = shade["box"]
        spots = {"y0": ((x0 + x1) // 2, y1 - 1), "y255": (x0 + 1, y0 + 1)}
        got = {}
        for key, pt in spots.items():
            self.click(*pt, method=method)
            img = capture_window(self.hwnd)
            ys = [find_knob(img, x, sl["top"], sl["bottom"]) for x in sl["x"]]
            if None in ys or max(ys) - min(ys) > 8:
                return None                       # knobs not all together: can't trust it
            got[key] = float(np.median(ys))
        if got["y0"] - got["y255"] < 40:
            return None
        cal = {"size": size, "x": sl["x"], "y0": got["y0"], "y255": got["y255"]}
        self.cfg["slider_cal"] = cal
        self.log("Learned the R/G/B readout.")
        return cal

    def _ring_click_at_angle(self, wheel, ang, rr, method):
        pt = int(wheel["cx"] + rr * math.cos(ang)), int(wheel["cy"] - rr * math.sin(ang))
        self.click(*pt, method=method)
        return pt

    def visual_color(self, r, g, b):
        """Pick the hue on the ring and the shade in the box, then read the exact R/G/B back
        from the sliders and keep nudging both until it matches."""
        import colorsys
        img, wheel, shade, source = self.see()
        if not wheel:
            raise RuntimeError("Couldn't see the color wheel.")
        if source == "layout":
            self.log("Using ASRRGBLED's known layout.")
        method = self.learn_click_method(img, wheel, shade)
        sl = self._sliders(img, wheel) if shade else None
        cal = self.calibrate_sliders(sl, shade, method, wheel) if sl else None
        if not sl:
            self.log("Color readout: couldn't find the R/G/B sliders. Saved color_check.png.")
            self._debug_picture("color_check.png", None, shade, wheel, "no sliders found")

        target = hue_of_rgb(r, g, b)
        pt = self.ring_point(capture_window(self.hwnd), wheel, target, source)
        self.click(*pt, method=method)
        if not shade:
            return
        img2 = capture_window(self.hwnd)
        shade2 = (detect_shade_box(img2) if not self.is_blank(img2) else None) or shade
        spot = shade2["point_for_rgb"](r, g, b)
        self.click(*spot, method=method)
        if not cal:
            self._debug_picture("last_color.png", sl, shade2, wheel, f"wanted {r},{g},{b} (no readout)")
            return

        # ---- fine-tune until the sliders say exactly the wanted color
        ang = math.atan2(-(pt[1] - wheel["cy"]), pt[0] - wheel["cx"])
        rr = math.hypot(pt[0] - wheel["cx"], pt[1] - wheel["cy"])
        turn = -1 if wheel.get("clockwise") else 1
        bx0, by0, bx1, by1 = shade2["box"]
        hsv_box = shade2.get("style") == "hsv"

        def parts(c):
            rr_, gg_, bb_ = (v / 255 for v in c)
            if hsv_box:
                h_, s_, v_ = colorsys.rgb_to_hsv(rr_, gg_, bb_)
                return h_, s_, v_
            h_, l_, s_ = colorsys.rgb_to_hls(rr_, gg_, bb_)
            return h_, s_, l_

        th, ts, tl = parts((r, g, b))
        got = self.read_rgb(sl, cal)
        sx, sy = float(spot[0]), float(spot[1])
        for _ in range(10):
            if got is None or max(abs(a - c) for a, c in zip(got, (r, g, b))) <= 3:
                break
            mh, ms, ml = parts(got)
            dh = ((th - mh + 0.5) % 1) - 0.5
            if ts > 0.15 and ms > 0.15 and abs(dh) > 0.006:
                ang += turn * dh * 2 * math.pi        # wrong hue: move along the ring
                self._ring_click_at_angle(wheel, ang, rr, method)
                self.click(int(sx), int(sy), method=method)
            else:                                     # right hue: fix the shade
                # the box may sit a few pixels off from where it was found; the picker
                # clamps clicks just past its edge, so allow a little overshoot
                sx = min(bx1 + 8, max(bx0 - 8, sx + (ts - ms) * (bx1 - bx0) * 0.8))
                sy = min(by1 + 8, max(by0 - 8, sy + (ml - tl) * (by1 - by0) * 0.8))
                self.click(int(sx), int(sy), method=method)
            got = self.read_rgb(sl, cal)
        if got:
            self.log(f"Set R{got[0]} G{got[1]} B{got[2]} (wanted R{r} G{g} B{b}).")
        self._debug_picture("last_color.png", sl, shade2, wheel, f"wanted {r},{g},{b} got {got}")

    def toggle_spot(self, img, wheel):
        """Where the on/off switch is. Remembered once seen (it can't be seen by color when off)."""
        pt = detect_toggle(img, wheel)
        if pt:
            self.cfg["toggle_rel"] = [(pt[0] - wheel["cx"]) / wheel["r"], (pt[1] - wheel["cy"]) / wheel["r"]]
            return pt
        rx, ry = self.cfg.get("toggle_rel") or KNOWN_LAYOUT["toggle_rel"]
        return int(wheel["cx"] + rx * wheel["r"]), int(wheel["cy"] + ry * wheel["r"])

    def set_switch(self, on):
        """Turn the header's LEDs on or off with the switch under the wheel. Returns True if it's
        now in the wanted state (as far as can be seen)."""
        img, wheel, shade, _ = self.see()
        if not wheel:
            return False
        method = self.learn_click_method(img, wheel, shade)
        pt = self.toggle_spot(img, wheel)
        for _ in range(2):
            now_on = toggle_is_on(capture_window(self.hwnd), pt)
            if now_on == on:
                return True
            self.click(*pt, method=method)
            time.sleep(0.3)
        return toggle_is_on(capture_window(self.hwnd), pt) == on

    def visual_black(self):
        """Full black: switch the LEDs off. (The shade box's darkest spot still glows dim white.)"""
        if self.set_switch(False):
            return
        self.log("Couldn't flip the on/off switch, using the darkest shade instead.")
        img, wheel, shade, source = self.see()
        if not shade:
            raise RuntimeError("Couldn't see the shade box.")
        method = self.learn_click_method(img, wheel, shade)
        x0, y0, x1, y1 = shade["box"]
        self.click(*shade["black"], method=method)
        self.click(int((x0 + x1) / 2), y1 - 1, method=method)    # very bottom edge

    # ---------------- header and apply
    def _read_screen(self):
        try:
            return ocr_lines(capture_window(self.hwnd))
        except RuntimeError as e:
            self.log(str(e))
            return []

    def _click_spot(self, x, y):
        """Click a spot found by reading the screen, using whichever click method works."""
        method = self.cfg.get("click_method", "auto")
        if method == "auto":
            img, wheel, shade, _ = self.see()
            method = self.learn_click_method(img, wheel, shade)
        self.click(x, y, method=method)

    def press_header(self):
        name = self.cfg.get("header_name") or "Addressable"
        el = self.find_named(name)
        if el:
            self.click_element(el)
        else:
            lines = self._read_screen()
            spot = None
            for wanted in (name, "Addressable RGB", "Addressable", "ARGB", "ADDR"):
                spot = ocr_find(lines, wanted)
                if spot:
                    break
            if not spot:
                size = capture_window(self.hwnd).size
                if layout_fits(size):
                    spot = layout_point(size, "header")
            if spot:
                self._click_spot(*spot)
            else:
                self.log(f"Couldn't find \"{name}\" on screen, using whatever header is selected.")
        time.sleep(0.6)
        self.changed()

    def press_apply(self):
        name = self.cfg.get("apply_name") or "Apply"
        el = self.find_named(name, exact=True, types=("Button",)) or \
            self.find_named(name, types=("Button",)) or self.find_named(name)
        if el and self.click_element(el):
            time.sleep(0.6)
            return
        lines = self._read_screen()
        spot = ocr_find(lines, name) or ocr_find(lines, "Apply All") or ocr_find(lines, "Apply")
        if not spot:
            size = capture_window(self.hwnd).size
            if layout_fits(size):
                spot = layout_point(size, "apply")
        if not spot:
            raise RuntimeError(f"Couldn't find the \"{name}\" button.")
        self._click_spot(*spot)
        time.sleep(0.6)


def apply_color(cfg, hexc, log):
    """Set the Addressable RGB LED header to one color. Black = lights off.
    If ASRRGBLED crashes, it's restarted and tried once more."""
    for attempt in (1, 2):
        try:
            return _apply_color_once(cfg, hexc, log)
        except AsrCrashed:
            kill_asr(cfg)
            if attempt == 2:
                raise RuntimeError("ASRRGBLED crashed twice. Try opening it yourself to see if it crashes on its own.")
            log("ASRRGBLED crashed, restarting it...")
            time.sleep(1.5)


def _apply_color_once(cfg, hexc, log):
    r, g, b = hex_rgb(hexc)
    with AsrSession(cfg, log) as s:
        s.press_header()
        s.check()
        if is_black(hexc):
            done = False
            try:
                s.visual_black()                      # flips the on/off switch to off
                done = True
            except RuntimeError as e:
                log(str(e))
            if not done:
                s.choose_mode(["Static", "Fixed", "Solid"])
                done = s.set_rgb_boxes(0, 0, 0) or s.set_brightness(0)
            if not done:
                raise RuntimeError("Couldn't set black.")
        else:
            if not s.set_switch(True):
                log("Couldn't check the on/off switch.")
            s.choose_mode(["Static", "Fixed", "Solid"])
            s.set_brightness(100)
            if not s.set_rgb_boxes(r, g, b):
                s.visual_color(r, g, b)
        s.check()
        s.press_apply()
        time.sleep(0.3)
        s.check()
    save_config(cfg)
    return True


def scan_asr(cfg, log):
    """Saves what the app sees: controls, child windows, text on screen, a screenshot,
    and what it detected. Keeps going even if parts fail."""
    lines = []
    with AsrSession(cfg, log) as s:
        try:
            s.press_header()
        except Exception as e:
            lines.append(f"(header press failed: {e})")
        lines.append("== Controls (UI Automation) ==")
        for el in s.elements():
            try:
                ei = el.element_info
                try:
                    val = el.get_value()
                except Exception:
                    val = ""
                rc = el.rectangle()
                lines.append(f"{ei.control_type:<12} name={ei.name!r} id={ei.automation_id!r} "
                             f"value={val!r} size={rc.right - rc.left}x{rc.bottom - rc.top} class={ei.class_name!r}")
            except Exception as e:
                lines.append(f"(element error: {e})")

        lines.append("")
        lines.append("== Child windows ==")
        kids = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(ch, _):
            cls = ctypes.create_unicode_buffer(64)
            txt = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(ch, cls, 64)
            user32.GetWindowTextW(ch, txt, 256)
            r = wintypes.RECT()
            user32.GetWindowRect(ch, ctypes.byref(r))
            kids.append(f"{cls.value:<24} text={txt.value!r} size={r.right - r.left}x{r.bottom - r.top}")
            return True
        user32.EnumChildWindows(s.hwnd, cb, 0)
        lines += kids or ["(none)"]

        img = capture_window(s.hwnd)
        lines.append("")
        lines.append("== Text on screen ==")
        try:
            for ln in ocr_lines(img):
                lines.append(f"{ln['text']!r} at {tuple(int(v) for v in ln['box'])}")
        except Exception as e:
            lines.append(f"(text reader failed: {e})")

        wheel, shade = detect_all(img)
        picks = []
        if wheel:
            p = wheel_point_for_hue(img, wheel, hue_of_rgb(0, 255, 0))
            if p:
                picks.append((p, (255, 255, 255)))
        if shade:
            picks += [(shade["black"], (255, 0, 0)), (shade["bright"], (255, 255, 0))]
        os.makedirs(CONFIG_DIR, exist_ok=True)
        img.save(os.path.join(CONFIG_DIR, "asr_window.png"))
        draw_debug(img, wheel, shade, picks, os.path.join(CONFIG_DIR, "asr_detected.png"))
        lines.append("")
        lines.append(f"Wheel: {'found' if wheel else 'NOT found'}  Shade box: {'found' if shade else 'NOT found'}")
        with open(os.path.join(CONFIG_DIR, "asr_controls.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    os.startfile(CONFIG_DIR)
    return wheel is not None, shade is not None


# ================================================================ updates (GitHub Releases)

def _ver(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def latest_release():
    """(version, download url, notes) of the newest release, or None."""
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"User-Agent": "RGB-Scheduler", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    url = next((a["browser_download_url"] for a in data.get("assets", [])
                if a.get("name", "").lower() == UPDATE_ASSET.lower()), None)
    if not url:
        url = next((a["browser_download_url"] for a in data.get("assets", [])
                    if a.get("name", "").lower().endswith(".exe")), None)
    ver = (data.get("tag_name") or "").lstrip("vV")
    return (ver, url, (data.get("body") or "").strip()) if ver and url else None


def download_update(url, dest, progress=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "RGB-Scheduler"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    with open(tmp, "rb") as f:
        if f.read(2) != b"MZ" or os.path.getsize(tmp) < 1_000_000:
            os.remove(tmp)
            raise RuntimeError("The download wasn't a working app.")
    os.replace(tmp, dest)


def swap_and_restart(new_exe):
    """Hand off to a tiny hidden script: wait for this app to close, put the new .exe in
    place of this one, and start it again (still as admin, no prompt)."""
    cur = sys.executable
    bat = os.path.join(CONFIG_DIR, "update.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f"""@echo off
set n=0
:wait
tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul
if errorlevel 1 goto go
set /a n+=1
if %n% gtr 60 goto go
ping -n 2 127.0.0.1 >nul
goto wait
:go
set m=0
:move
move /y "{new_exe}" "{cur}" >nul 2>&1
if not errorlevel 1 goto start
set /a m+=1
if %m% gtr 20 goto start
ping -n 2 127.0.0.1 >nul
goto move
:start
start "" "{cur}"
del "%~f0"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=NO_WINDOW, close_fds=True)


# ================================================================ schedule math

def parse_hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def active_entries(cfg):
    return sorted([e for e in cfg["schedule"] if e.get("on", True)], key=lambda e: e["time"])


def latest_entry(cfg, now=None):
    """The schedule entry that should be showing right now: (entry, when it started)."""
    now = now or dt.datetime.now()
    best = None
    for e in active_entries(cfg):
        t = parse_hm(e["time"])
        for back in (0, 1):
            when = dt.datetime.combine(now.date() - dt.timedelta(days=back), t)
            if when <= now and (best is None or when > best[1]):
                best = (e, when)
    return best


def next_entry(cfg, now=None):
    now = now or dt.datetime.now()
    best = None
    for e in active_entries(cfg):
        t = parse_hm(e["time"])
        for ahead in (0, 1):
            when = dt.datetime.combine(now.date() + dt.timedelta(days=ahead), t)
            if when > now and (best is None or when < best[1]):
                best = (e, when)
    return best


def fmt12(hm):
    return parse_hm(hm).strftime("%I:%M %p").lstrip("0")


def to24(hour12, minute, ampm):
    h = int(hour12) % 12 + (12 if ampm == "PM" else 0)
    return f"{h:02d}:{int(minute):02d}"


def split12(hm):
    t = parse_hm(hm)
    return str(t.hour % 12 or 12), f"{t.minute:02d}", "PM" if t.hour >= 12 else "AM"


def day_segments(cfg):
    """Colored pieces of a 24-hour day: [(start_minute, end_minute, color)]."""
    ents = active_entries(cfg)
    if not ents:
        return []
    mins = [(parse_hm(e["time"]).hour * 60 + parse_hm(e["time"]).minute, e["color"]) for e in ents]
    segs = [(0, mins[0][0], mins[-1][1])]   # after midnight it's still the last color of the day
    for i, (m, c) in enumerate(mins):
        end = mins[i + 1][0] if i + 1 < len(mins) else 1440
        segs.append((m, end, c))
    return [sg for sg in segs if sg[1] > sg[0]]


def new_id():
    return f"e{int(time.time() * 1000) % 10_000_000_000}"


# ================================================================ app

class App:
    def __init__(self, start_minimized, selftest=False):
        import customtkinter as ctk
        import pystray
        self.ctk, self.pystray = ctk, pystray

        self.cfg = load_config()
        if not self.cfg["asr_path"]:
            self.cfg["asr_path"] = find_asr_exe()
        self.run_lock = threading.Lock()
        self.update_info = None
        self.quitting = False
        self.last_fail = 0.0
        self.fail_count = 0
        self.fail_color = None

        ctk.set_appearance_mode("dark")
        self.icon_img = ensure_icon_file()

        self.root = ctk.CTk(fg_color=BG)
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("480x740")
        self.root.minsize(470, 600)
        self.root.after(250, lambda: self.root.iconbitmap(ICON_PATH))
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Unmap>", self._on_unmap)

        self._build_ui()
        self._start_tray()

        self.override_until = 0.0      # while a Test preview is showing
        self.override_color = ""
        self.wake = threading.Event()
        if self.cfg["apply_on_launch"]:
            self.cfg["_last_color"] = ""   # don't trust what was set before the PC was off
        save_config(self.cfg)

        self.refresh()
        self._tick()
        self.log("Started.")
        if selftest:
            self._selftest()
            return
        self.console_hider = WindowHider(self.cfg, consoles_only=True).start()   # ASRock command prompts, always
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        threading.Thread(target=self._listen_for_show, daemon=True).start()
        self.root.after(8000, self._auto_update_tick)
        if start_minimized:
            self.root.withdraw()

    def _selftest(self):
        """Used by the build: open every screen once and fail loudly on any error."""
        errors = []
        self.root.report_callback_exception = lambda *a: errors.append(a[1])

        def steps():
            try:
                self.show_page("settings")
                self.root.update()
                self._refresh_version_labels()
                self.show_page("home")
                self.open_editor(None)
                self.root.update()
                self.open_editor(self.cfg["schedule"][0])
                self.root.update()
                self._draw_bar()
                assert latest_entry(self.cfg) is not None
            except Exception as e:
                errors.append(e)
            self.selftest_errors = errors
            self.quit()
        self.root.after(1500, steps)

    # ------------------------------------------------------------ layout
    def _build_ui(self):
        ctk = self.ctk

        # Header
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 8))
        pfp = ctk.CTkImage(light_image=self.icon_img, dark_image=self.icon_img, size=(48, 48))
        ctk.CTkLabel(header, image=pfp, text="").pack(side="left")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=12)
        ctk.CTkLabel(titles, text=APP_NAME, font=("Segoe UI Semibold", 20)).pack(anchor="w")
        ctk.CTkLabel(titles, text="Fan light colors on a timer", text_color=MUTED,
                     font=("Segoe UI", 12)).pack(anchor="w")
        self.gear_btn = ctk.CTkButton(header, text="⚙", width=36, height=34, fg_color=CARD_BORDER,
                                      hover_color="#323845", font=("Segoe UI", 16), command=self.toggle_settings)
        self.gear_btn.pack(side="right")
        sw = ctk.CTkFrame(header, fg_color="transparent")
        sw.pack(side="right", padx=(0, 12))
        self.enabled_var = ctk.BooleanVar(value=self.cfg["enabled"])
        ctk.CTkSwitch(sw, text="", variable=self.enabled_var, progress_color=GREEN,
                      switch_width=52, switch_height=26, command=self._toggle_enabled).pack()
        self.enabled_lbl = ctk.CTkLabel(sw, text="", text_color=MUTED, font=("Segoe UI", 11))
        self.enabled_lbl.pack()
        self.update_badge = ctk.CTkButton(header, text="", height=30, fg_color=GREEN, hover_color="#16a34a",
                                          text_color="#06200f", font=("Segoe UI Semibold", 12),
                                          command=self.install_update)   # shown when an update exists

        self.body = ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.pack(fill="both", expand=True)
        self.home = ctk.CTkFrame(self.body, fg_color="transparent")
        self.home.pack(fill="both", expand=True)
        self.page = "home"

        # Right now / next + day bar
        now_card = self._card(self.home)
        top = ctk.CTkFrame(now_card, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(top, text="RIGHT NOW", text_color=MUTED, font=("Segoe UI Semibold", 11)).pack(side="left")
        ctk.CTkLabel(top, text="NEXT", text_color=MUTED, font=("Segoe UI Semibold", 11)).pack(side="right")
        mid = ctk.CTkFrame(now_card, fg_color="transparent")
        mid.pack(fill="x", padx=16)
        self.now_dot = ctk.CTkLabel(mid, text="●", font=("Segoe UI", 26))
        self.now_dot.pack(side="left")
        self.now_lbl = ctk.CTkLabel(mid, text="", font=("Segoe UI Semibold", 18))
        self.now_lbl.pack(side="left", padx=(6, 0))
        self.next_lbl = ctk.CTkLabel(mid, text="", font=("Segoe UI", 13), text_color="#cfd5df", justify="right")
        self.next_lbl.pack(side="right")
        self.next_dot = ctk.CTkLabel(mid, text="●", font=("Segoe UI", 16))
        self.next_dot.pack(side="right", padx=(0, 4))

        self.preview_row = ctk.CTkFrame(now_card, fg_color="transparent")
        self.preview_row.pack(fill="x", padx=16, pady=(6, 0))
        self.preview_lbl = ctk.CTkLabel(self.preview_row, text="", text_color="#fbbf24", font=("Segoe UI", 12))
        self.preview_lbl.pack(side="left")
        self.sync_btn = ctk.CTkButton(self.preview_row, text="⟳  Sync now", width=104, height=28,
                                      fg_color=CARD_BORDER, hover_color="#323845", font=("Segoe UI Semibold", 12),
                                      command=self.sync_now)
        self.sync_btn.pack(side="right")

        import tkinter as tk
        self.bar = tk.Canvas(now_card, height=46, bg=CARD, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", padx=16, pady=(10, 14))
        self.bar.bind("<Configure>", lambda _e: self._draw_bar())

        # Activity log (packed before the schedule so it always keeps its space at the bottom)
        self.logbox = ctk.CTkTextbox(self.home, height=70, fg_color=CARD, border_color=CARD_BORDER,
                                     border_width=1, font=("Consolas", 11), text_color=MUTED)
        self.logbox.pack(fill="x", side="bottom", padx=20, pady=(4, 14))
        self.logbox.configure(state="disabled")

        # Schedule list
        sched = self._card(self.home, expand=True)
        st = ctk.CTkFrame(sched, fg_color="transparent")
        st.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(st, text="Schedule", font=("Segoe UI Semibold", 15)).pack(side="left")
        ctk.CTkButton(st, text="+  Add a time", width=110, height=30, fg_color=GREEN, hover_color="#16a34a",
                      text_color="#06200f", font=("Segoe UI Semibold", 12),
                      command=lambda: self.open_editor(None)).pack(side="right")
        ctk.CTkLabel(sched, text="At each time, the fans switch to that color and stay until the next one.",
                     text_color=MUTED, font=("Segoe UI", 11), wraplength=400, justify="left").pack(anchor="w", padx=16)
        self.rows = ctk.CTkScrollableFrame(sched, fg_color="transparent", height=170)
        self.rows.pack(fill="both", expand=True, padx=8, pady=(6, 10))

        self._build_settings_page()

    # ------------------------------------------------------------ pages
    def show_page(self, name):
        if name == "settings":
            self._save_names()
            self.home.pack_forget()
            self.settings_page.pack(fill="both", expand=True)
            self.gear_btn.configure(text="✕  Close", width=84)
            self._refresh_version_labels()
        else:
            self._save_names()
            self.settings_page.pack_forget()
            self.home.pack(fill="both", expand=True)
            self.gear_btn.configure(text="⚙", width=36)
        self.page = name

    def toggle_settings(self):
        self.show_page("home" if self.page == "settings" else "settings")

    def _section(self, parent, title, note=None):
        ctk = self.ctk
        card = self._card(parent)
        ctk.CTkLabel(card, text=title, font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=16, pady=(12, 0))
        if note:
            ctk.CTkLabel(card, text=note, text_color=MUTED, font=("Segoe UI", 11), wraplength=380,
                         justify="left").pack(anchor="w", padx=16)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=(6, 12))
        return body

    def _switch(self, parent, text, key, command=None):
        var = self.ctk.BooleanVar(value=bool(self.cfg.get(key)))

        def changed():
            if command:
                command(var)
            else:
                self.cfg[key] = var.get()
                save_config(self.cfg)
        self.ctk.CTkSwitch(parent, text=text, variable=var, progress_color=GREEN, font=("Segoe UI", 12),
                           command=changed).pack(anchor="w", pady=3)
        return var

    def _build_settings_page(self):
        ctk = self.ctk
        page = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.settings_page = page
        btn = dict(height=30, fg_color=CARD_BORDER, hover_color="#323845", font=("Segoe UI Semibold", 12))

        # Updates
        up = self._section(page, "Updates")
        row = ctk.CTkFrame(up, fg_color="transparent")
        row.pack(fill="x")
        self.version_lbl = ctk.CTkLabel(row, text="", font=("Segoe UI", 12))
        self.version_lbl.pack(side="left")
        self.update_now_btn = ctk.CTkButton(row, text="Update now", width=104, fg_color=GREEN,
                                            hover_color="#16a34a", text_color="#06200f",
                                            font=("Segoe UI Semibold", 12), height=30, command=self.install_update)
        ctk.CTkButton(row, text="Check for updates", width=140, command=lambda: self.check_updates(manual=True),
                      **btn).pack(side="right")
        self.update_status = ctk.CTkLabel(up, text="", text_color=MUTED, font=("Segoe UI", 11))
        self.update_status.pack(anchor="w")
        self._switch(up, "Check for updates automatically", "auto_update_check")

        # Startup
        st = self._section(page, "Startup")
        self._switch(st, "Start with Windows (hidden in the tray)", "start_with_windows",
                     command=self._toggle_startup)
        self._switch(st, "Set the right color when the app starts", "apply_on_launch")

        # Polychrome
        pc = self._section(page, "ASRock Polychrome",
                           "RGB Scheduler opens Polychrome invisibly, sets the color, and closes it again.")
        row = ctk.CTkFrame(pc, fg_color="transparent")
        row.pack(fill="x", pady=(0, 4))
        self.path_var = ctk.StringVar(value=self.cfg["asr_path"])
        e = ctk.CTkEntry(row, textvariable=self.path_var, placeholder_text="Path to ASRRGBLED.exe", height=30)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<FocusOut>", lambda _e: self._save_names())
        ctk.CTkButton(row, text="Browse", width=72, command=self._browse, **btn).pack(side="left", padx=(6, 0))
        self._switch(pc, "Close Polychrome after each change (slower, shows its loading screen)", "close_after")
        ctk.CTkButton(pc, text="Show Polychrome", width=140, command=self.show_polychrome,
                      **btn).pack(anchor="w", pady=(6, 0))

        # Troubleshooting
        tr = self._section(page, "Troubleshooting",
                           "Only if colors come out wrong. Re-learn makes it re-check Polychrome's "
                           "layout next time. Scan saves a report and screenshot to send to Claude.")
        row = ctk.CTkFrame(tr, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="Re-learn", width=96, command=lambda: (self.show_page("home"), self.relearn()),
                      **btn).pack(side="left")
        ctk.CTkButton(row, text="Scan", width=72, command=lambda: (self.show_page("home"), self.scan()),
                      **btn).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Open folder", width=100, command=lambda: os.startfile(CONFIG_DIR),
                      **btn).pack(side="left")

    def _refresh_version_labels(self):
        self.version_lbl.configure(text=f"Version {APP_VERSION}")
        if self.update_info:
            self.update_now_btn.configure(text=f"Update to {self.update_info[0]}")
            self.update_now_btn.pack(side="right", padx=(6, 0))
        else:
            self.update_now_btn.pack_forget()

    def relearn(self):
        for k in ("slider_cal", "toggle_rel", "sliders_ok"):
            self.cfg.pop(k, None)
        self.cfg["click_method"] = "auto"
        save_config(self.cfg)
        self.log("Will re-learn Polychrome's layout on the next color change.")

    def _card(self, parent, expand=False, pack=True):
        card = self.ctk.CTkFrame(parent, fg_color=CARD, border_color=CARD_BORDER,
                                 border_width=1, corner_radius=14)
        if pack:
            card.pack(fill="both" if expand else "x", expand=expand,
                      padx=10 if parent is getattr(self, "settings_page", None) else 20, pady=6)
        return card

    # ------------------------------------------------------------ drawing
    def refresh(self):
        self._draw_rows()
        self._draw_status()
        self._draw_bar()
        if hasattr(self, "tray"):
            self.tray.update_menu()

    def _tick(self):
        if self.quitting:
            return
        self._draw_status()
        self._draw_bar()
        # tick every second while a preview countdown is showing
        self.root.after(1000 if time.time() < self.override_until else 15_000, self._tick)

    @staticmethod
    def _dot_color(hexc):
        return "#2a2f3a" if is_black(hexc) else hexc

    def _draw_status(self):
        self.enabled_lbl.configure(text="On" if self.cfg["enabled"] else "Paused")
        cur = latest_entry(self.cfg)
        if cur:
            self.now_dot.configure(text_color=self._dot_color(cur[0]["color"]))
            self.now_lbl.configure(text=color_name(cur[0]["color"]))
        else:
            self.now_dot.configure(text_color=MUTED)
            self.now_lbl.configure(text="Nothing scheduled")
        nx = next_entry(self.cfg)
        if nx and self.cfg["enabled"]:
            day = "" if nx[1].date() == dt.date.today() else " tomorrow"
            self.next_dot.configure(text_color=self._dot_color(nx[0]["color"]))
            self.next_lbl.configure(text=f"{color_name(nx[0]['color'])}\nat {fmt12(nx[0]['time'])}{day}")
        else:
            self.next_dot.configure(text_color=CARD)
            self.next_lbl.configure(text="Paused" if not self.cfg["enabled"] else "")
        left = int(self.override_until - time.time())
        if left > 0 and self.override_color:
            self.preview_lbl.configure(text=f"Testing {color_name(self.override_color)} · back to schedule in {left}s")
        elif cur and self.cfg.get("_last_color") and self.cfg["_last_color"] != cur[0]["color"]:
            self.preview_lbl.configure(text=f"Fans may be showing {color_name(self.cfg['_last_color'])}")
        else:
            self.preview_lbl.configure(text="")

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        w = max(c.winfo_width(), 200)
        bar_top, bar_h = 6, 18
        segs = day_segments(self.cfg)
        if not segs:
            c.create_rectangle(0, bar_top, w, bar_top + bar_h, fill="#22262e", outline="")
        for start, end, col in segs:
            x0, x1 = start / 1440 * w, end / 1440 * w
            fill = "#050506" if is_black(col) else col
            c.create_rectangle(x0, bar_top, x1, bar_top + bar_h, fill=fill, outline="")
        c.create_rectangle(0, bar_top, w - 1, bar_top + bar_h, outline=CARD_BORDER)
        # entry tick marks
        for e in active_entries(self.cfg):
            t = parse_hm(e["time"])
            x = (t.hour * 60 + t.minute) / 1440 * w
            c.create_line(x, bar_top - 3, x, bar_top + bar_h + 3, fill="#e5e7eb", width=2)
        # "now" marker
        now = dt.datetime.now()
        x = (now.hour * 60 + now.minute) / 1440 * w
        c.create_polygon(x - 5, 0, x + 5, 0, x, 6, fill="#ffffff", outline="")
        # hour labels
        for hrs, label in ((0, "12 AM"), (6, "6 AM"), (12, "12 PM"), (18, "6 PM"), (24, "12 AM")):
            x = hrs / 24 * w
            anchor = "nw" if hrs == 0 else ("ne" if hrs == 24 else "n")
            c.create_text(x, bar_top + bar_h + 6, text=label, fill=MUTED, font=("Segoe UI", 8), anchor=anchor)

    def _draw_rows(self):
        ctk = self.ctk
        for wdg in self.rows.winfo_children():
            wdg.destroy()
        ents = sorted(self.cfg["schedule"], key=lambda e: e["time"])
        if not ents:
            ctk.CTkLabel(self.rows, text="No times yet. Click  + Add a time.", text_color=MUTED).pack(pady=20)
            return
        cur = latest_entry(self.cfg)
        cur_id = cur[0]["id"] if cur else None
        for e in ents:
            active = e["id"] == cur_id
            row = ctk.CTkFrame(self.rows, fg_color="#1e222b" if active else "#1a1d24",
                               border_color=GREEN if active else CARD_BORDER, border_width=1, corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)
            dim = not e.get("on", True)
            swatch = ctk.CTkButton(row, text="", width=30, height=30, corner_radius=15,
                                   fg_color=self._dot_color(e["color"]), hover_color=self._dot_color(e["color"]),
                                   border_width=2, border_color="#3a3f4b", command=lambda en=e: self.open_editor(en))
            swatch.pack(side="left", padx=(10, 8), pady=8)
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.pack(side="left")
            t = ctk.CTkLabel(txt, text=fmt12(e["time"]), font=("Segoe UI Semibold", 15),
                             text_color=MUTED if dim else "#f3f4f6", anchor="w")
            t.pack(anchor="w")
            sub = color_name(e["color"]) + ("  •  now" if active else "") + ("  •  skipped" if dim else "")
            ctk.CTkLabel(txt, text=sub, font=("Segoe UI", 11), text_color=GREEN if active else MUTED,
                         anchor="w").pack(anchor="w")
            for wdg in (t, txt):
                wdg.bind("<Button-1>", lambda _ev, en=e: self.open_editor(en))

            ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent", hover_color="#3b1d22",
                          text_color=MUTED, command=lambda en=e: self.delete_entry(en)).pack(side="right", padx=(0, 8))
            ctk.CTkButton(row, text="Edit", width=46, height=28, fg_color=CARD_BORDER, hover_color="#323845",
                          command=lambda en=e: self.open_editor(en)).pack(side="right", padx=3)
            ctk.CTkButton(row, text="▶ Test", width=62, height=28, fg_color=CARD_BORDER, hover_color="#323845",
                          command=lambda en=e: self.run_color(en["color"])).pack(side="right", padx=3)
            on_var = ctk.BooleanVar(value=e.get("on", True))
            ctk.CTkSwitch(row, text="", width=40, variable=on_var, progress_color=GREEN, switch_width=34,
                          switch_height=18, command=lambda en=e, v=on_var: self._toggle_entry(en, v)).pack(side="right")

    # ------------------------------------------------------------ editor
    def open_editor(self, entry):
        ctk = self.ctk
        win = ctk.CTkToplevel(self.root, fg_color=BG)
        win.title("Edit time" if entry else "Add a time")
        win.geometry("420x530")
        win.resizable(False, False)
        win.transient(self.root)
        win.after(300, lambda: (win.iconbitmap(ICON_PATH), win.grab_set(), win.lift(), win.focus_force()))

        chosen = {"color": (entry["color"] if entry else "#00FF00").upper()}
        start = entry["time"] if entry else "12:00"

        ctk.CTkLabel(win, text="Edit time" if entry else "Add a time", font=("Segoe UI Semibold", 18)).pack(
            anchor="w", padx=20, pady=(18, 10))

        # Time
        tcard = self._card(win)
        ctk.CTkLabel(tcard, text="AT THIS TIME", text_color=MUTED, font=("Segoe UI Semibold", 11)).pack(
            anchor="w", padx=14, pady=(12, 4))
        trow = ctk.CTkFrame(tcard, fg_color="transparent")
        trow.pack(anchor="w", padx=14, pady=(0, 12))
        h, m, ap = split12(start)
        hv, mv, av = ctk.StringVar(value=h), ctk.StringVar(value=m), ctk.StringVar(value=ap)
        mins = [f"{i:02d}" for i in range(0, 60, 5)]
        if m not in mins:
            mins = sorted(set(mins + [m]))
        opts = dict(height=34, fg_color=CARD_BORDER, button_color=CARD_BORDER, button_hover_color="#323845",
                    font=("Segoe UI Semibold", 15), dropdown_font=("Segoe UI", 13))
        ctk.CTkOptionMenu(trow, values=[str(i) for i in range(1, 13)], variable=hv, width=70, **opts).pack(side="left")
        ctk.CTkLabel(trow, text=":", font=("Segoe UI Semibold", 18)).pack(side="left", padx=4)
        ctk.CTkOptionMenu(trow, values=mins, variable=mv, width=70, **opts).pack(side="left")
        ctk.CTkOptionMenu(trow, values=["AM", "PM"], variable=av, width=74, **opts).pack(side="left", padx=(8, 0))

        # Color
        ccard = self._card(win)
        ctk.CTkLabel(ccard, text="SWITCH THE FANS TO", text_color=MUTED, font=("Segoe UI Semibold", 11)).pack(
            anchor="w", padx=14, pady=(12, 6))
        grid = ctk.CTkFrame(ccard, fg_color="transparent")
        grid.pack(padx=10)
        swatches = {}

        def select(hexc):
            chosen["color"] = hexc.upper()
            for hx, b in swatches.items():
                b.configure(border_color="#ffffff" if hx == chosen["color"] else "#3a3f4b",
                            border_width=3 if hx == chosen["color"] else 2)
            preview.configure(fg_color=self._dot_color(chosen["color"]))
            preview_lbl.configure(text=color_name(chosen["color"]))

        for i, (name, hx) in enumerate(PRESETS):
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=i // 5, column=i % 5, padx=6, pady=4)
            b = ctk.CTkButton(cell, text="", width=40, height=40, corner_radius=20,
                              fg_color=self._dot_color(hx), hover_color=self._dot_color(hx),
                              border_width=2, border_color="#3a3f4b", command=lambda x=hx: select(x))
            b.pack()
            ctk.CTkLabel(cell, text=name.replace(" (black)", ""), font=("Segoe UI", 10), text_color=MUTED).pack()
            swatches[hx] = b

        prow = ctk.CTkFrame(ccard, fg_color="transparent")
        prow.pack(fill="x", padx=14, pady=(8, 12))
        preview = ctk.CTkButton(prow, text="", width=26, height=26, corner_radius=13, border_width=1,
                                border_color="#3a3f4b", hover=False, fg_color=self._dot_color(chosen["color"]))
        preview.pack(side="left")
        preview_lbl = ctk.CTkLabel(prow, text="", font=("Segoe UI Semibold", 13))
        preview_lbl.pack(side="left", padx=8)

        def custom():
            from tkinter import colorchooser
            c = colorchooser.askcolor(color=chosen["color"], title="Pick any color", parent=win)[1]
            if c:
                select(c)

        ctk.CTkButton(prow, text="🎨  Custom color…", width=130, height=30, fg_color=CARD_BORDER,
                      hover_color="#323845", command=custom).pack(side="right")
        select(chosen["color"])

        # Buttons
        brow = ctk.CTkFrame(win, fg_color="transparent")
        brow.pack(fill="x", padx=20, pady=(10, 16), side="bottom")

        def save():
            t = to24(hv.get(), mv.get(), av.get())
            # one color per time: saving over an existing time replaces it
            self.cfg["schedule"] = [e for e in self.cfg["schedule"]
                                    if e["time"] != t or (entry and e["id"] == entry["id"])]
            if entry:
                for e in self.cfg["schedule"]:
                    if e["id"] == entry["id"]:
                        e["time"], e["color"] = t, chosen["color"]
            else:
                self.cfg["schedule"].append({"id": new_id(), "time": t, "color": chosen["color"], "on": True})
            self._schedule_changed()
            self.log(f"Saved: {fmt12(t)} → {color_name(chosen['color'])}")
            win.destroy()

        ctk.CTkButton(brow, text="Save", width=110, height=36, fg_color=GREEN, hover_color="#16a34a",
                      text_color="#06200f", font=("Segoe UI Semibold", 14), command=save).pack(side="right")
        ctk.CTkButton(brow, text="Cancel", width=90, height=36, fg_color=CARD_BORDER, hover_color="#323845",
                      command=win.destroy).pack(side="right", padx=8)
        ctk.CTkButton(brow, text="▶ Try it", width=80, height=36, fg_color=CARD_BORDER, hover_color="#323845",
                      command=lambda: self.run_color(chosen["color"])).pack(side="left")
        if entry:
            ctk.CTkButton(brow, text="Delete", width=70, height=36, fg_color="transparent", hover_color="#3b1d22",
                          text_color="#f87171", command=lambda: (self.delete_entry(entry), win.destroy())).pack(
                side="left", padx=8)

    # ------------------------------------------------------------ schedule edits
    def _schedule_changed(self):
        """Save, redraw, and let the scheduler fix the color right away if it's wrong now."""
        save_config(self.cfg)
        self.refresh()
        self.wake.set()

    def _toggle_entry(self, entry, var):
        for e in self.cfg["schedule"]:
            if e["id"] == entry["id"]:
                e["on"] = var.get()
        self._schedule_changed()

    def delete_entry(self, entry):
        self.cfg["schedule"] = [e for e in self.cfg["schedule"] if e["id"] != entry["id"]]
        self.log(f"Removed {fmt12(entry['time'])}.")
        self._schedule_changed()

    # ------------------------------------------------------------ settings handlers
    def _toggle_enabled(self):
        self.cfg["enabled"] = self.enabled_var.get()
        self._schedule_changed()   # turning it back on catches up to the right color
        self.log("Schedule on." if self.cfg["enabled"] else "Schedule paused.")

    def _save_names(self):
        pv = getattr(self, "path_var", None)
        if pv is not None and pv.get().strip():
            self.cfg["asr_path"] = pv.get().strip()
        save_config(self.cfg)

    def _toggle_startup(self, var):
        on = var.get()
        ok, err = set_startup(on)
        if ok:
            self.cfg["start_with_windows"] = on
            save_config(self.cfg)
            self.log("Will start with Windows." if on else "Removed from startup.")
        else:
            var.set(not on)
            self.log(f"Startup setting failed: {err[:80]}")

    def _browse(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="Find ASRRGBLED", filetypes=[("Programs", "*.exe")])
        if path:
            self.path_var.set(path.replace("/", "\\"))
            self._save_names()

    def log(self, msg):
        line = f"{dt.datetime.now():%I:%M %p}  {msg}\n"

        def add():
            self.logbox.configure(state="normal")
            self.logbox.insert("end", line)
            lines = self.logbox.get("1.0", "end").splitlines()
            if len(lines) > 60:
                self.logbox.delete("1.0", f"{len(lines) - 60}.0")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
        self.root.after(0, add)

    # ------------------------------------------------------------ running
    @staticmethod
    def _com_job(job):
        import comtypes
        comtypes.CoInitialize()
        try:
            return job()
        finally:
            comtypes.CoUninitialize()

    def run_color(self, hexc, hold=60):
        """Show a color now. After `hold` seconds the scheduler puts the schedule's color back."""
        self.override_color = hexc
        self.override_until = time.time() + hold
        self.root.after(0, self._tick)
        threading.Thread(target=lambda: self._com_job(lambda: self._apply(hexc)), daemon=True).start()

    def sync_now(self):
        """Put the fans back on the color the schedule says right now."""
        self.override_until = 0.0
        self.override_color = ""
        ev = latest_entry(self.cfg)
        if not ev:
            self.log("Nothing scheduled.")
            return
        self.log("Syncing to schedule...")
        threading.Thread(target=lambda: self._com_job(lambda: self._apply(ev[0]["color"])), daemon=True).start()

    def _apply(self, hexc):
        if not self.run_lock.acquire(blocking=False):
            self.log("Already working on it...")
            return False
        try:
            self.log(f"Setting {color_name(hexc)}...")
            watchdog = threading.Timer(150, self._too_slow)
            watchdog.daemon = True
            watchdog.start()
            try:
                apply_color(self.cfg, hexc, self.log)
            finally:
                watchdog.cancel()
            self.cfg["_last_color"] = hexc
            save_config(self.cfg)
            self.log("Done.")
            self.root.after(0, self.refresh)
            return True
        except Exception as e:
            self.log(f"Didn't work: {e}")
            self.log("Saved last_fail.txt and last_fail.png in the RGBScheduler folder - send those.")
            return False
        finally:
            self.run_lock.release()

    # ------------------------------------------------------------ updates
    def _auto_update_tick(self):
        if self.quitting:
            return
        if self.cfg.get("auto_update_check", True):
            self.check_updates(manual=False)
        self.root.after(6 * 3600 * 1000, self._auto_update_tick)       # every 6 hours

    def _set_update_status(self, text):
        self.root.after(0, lambda: self.update_status.configure(text=text))

    def check_updates(self, manual=False):
        def job():
            if manual:
                self._set_update_status("Checking...")
            try:
                info = latest_release()
            except Exception as e:
                if manual:
                    self._set_update_status(f"Couldn't check: {str(e)[:60]}")
                return
            if info and _ver(info[0]) > _ver(APP_VERSION):
                self.update_info = info
                self._set_update_status(f"Version {info[0]} is ready." + (f"  {info[2][:120]}" if info[2] else ""))
                self.log(f"Update {info[0]} is available. Press Update in the top bar.")
            else:
                self.update_info = None
                if manual:
                    self._set_update_status("You're on the newest version.")

            def show():
                if self.update_info:
                    self.update_badge.configure(text=f"⬆  Update {self.update_info[0]}")
                    self.update_badge.pack(side="right", padx=(0, 10))
                else:
                    self.update_badge.pack_forget()
                self._refresh_version_labels()
            self.root.after(0, show)
        threading.Thread(target=job, daemon=True).start()

    def install_update(self):
        if not self.update_info:
            self.check_updates(manual=True)
            return
        if not getattr(sys, "frozen", False):
            self.log("Updating only works in the .exe version.")
            return
        ver, url, _ = self.update_info

        def job():
            if not self.run_lock.acquire(timeout=180):      # don't swap mid color change
                self.log("Busy setting a color, try again in a minute.")
                return
            try:
                self.log(f"Downloading update {ver}...")
                new_exe = os.path.join(os.path.dirname(sys.executable), "RGB Scheduler (update).exe")
                download_update(url, new_exe,
                                progress=lambda pc: self._set_update_status(f"Downloading... {pc}%"))
                self.log("Installing and restarting...")
                swap_and_restart(new_exe)
                self.root.after(500, self.quit)
            except Exception as e:
                self.log(f"Update failed: {e}")
                self.run_lock.release()
        threading.Thread(target=job, daemon=True).start()

    def show_polychrome(self):
        """Bring the hidden Polychrome window back so you can use it yourself."""
        def job():
            if not self.run_lock.acquire(timeout=120):
                return
            try:
                hwnd = find_asr_hwnd(self.cfg)
                if not hwnd:
                    path = self.cfg.get("asr_path") or find_asr_exe()
                    if path:
                        subprocess.Popen([path], cwd=os.path.dirname(path), creationflags=NO_WINDOW)
                    return
                user32.SetWindowLongW(hwnd, -20, ORIGINAL_EX.get(hwnd, user32.GetWindowLongW(hwnd, -20) & ~0x80080))
                user32.ShowWindow(hwnd, 9)                     # SW_RESTORE
                user32.SetWindowPos(hwnd, 0, 100, 100, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
                user32.SetForegroundWindow(hwnd)
            finally:
                self.run_lock.release()
        threading.Thread(target=job, daemon=True).start()

    def _too_slow(self):
        """If setting a color gets stuck, close ASRRGBLED so the stuck step fails and it can retry."""
        self.log("ASRRGBLED got stuck, closing it...")
        kill_asr(self.cfg)

    def scan(self):
        def job():
            if not self.run_lock.acquire(blocking=False):
                return
            try:
                self.log("Scanning ASRRGBLED...")
                self.cfg.pop("sliders_ok", None)
                w, s = scan_asr(self.cfg, self.log)
                self.log(f"Wheel {'found' if w else 'NOT found'}, shade box {'found' if s else 'NOT found'}.")
            except Exception as e:
                self.log(f"Scan failed: {e}")
            finally:
                self.run_lock.release()
        self._save_names()
        threading.Thread(target=lambda: self._com_job(job), daemon=True).start()

    def _scheduler_loop(self):
        """Every couple of seconds: if the fans aren't on the schedule's color, fix them."""
        import comtypes
        comtypes.CoInitialize()
        while not self.quitting:
            try:
                ev = latest_entry(self.cfg)
                want = ev[0]["color"] if ev else None
                if want != self.fail_color:          # new color to set: start retries fresh
                    self.fail_color, self.fail_count = want, 0
                # after a failure wait longer each time: 1, 5, 15, then every 30 minutes
                wait = (0, 60, 300, 900)[self.fail_count] if self.fail_count < 4 else 1800
                if (self.cfg["enabled"] and want and time.time() >= self.override_until
                        and want != self.cfg.get("_last_color") and time.time() - self.last_fail > wait
                        and not self.run_lock.locked()):
                    self.override_color = ""
                    if self._apply(want):
                        self.fail_count = 0
                    else:
                        self.last_fail = time.time()
                        self.fail_count += 1
                        self.log(f"Will try again in {(60, 300, 900)[self.fail_count - 1] // 60 if self.fail_count < 4 else 30} min.")
                    self.root.after(0, self.refresh)
            except Exception as e:
                self.log(f"Scheduler error: {e}")
            self.wake.wait(2)
            self.wake.clear()

    # ------------------------------------------------------------ tray / window
    def _start_tray(self):
        p = self.pystray

        def lights_off_until_next():
            nx = next_entry(self.cfg)
            hold = (nx[1] - dt.datetime.now()).total_seconds() if nx else 3600
            self.run_color("#000000", hold=max(hold, 60))

        menu = p.Menu(
            p.MenuItem("Open", lambda: self.root.after(0, self.show), default=True),
            p.MenuItem("Schedule on", lambda: self.root.after(0, self._tray_toggle),
                       checked=lambda _: self.cfg["enabled"]),
            p.MenuItem("Sync to schedule now", lambda: self.root.after(0, self.sync_now)),
            p.MenuItem("Lights off until next change", lights_off_until_next),
            p.MenuItem("Check for updates", lambda: self.root.after(0, lambda: (self.show(),
                       self.show_page("settings"), self.check_updates(manual=True)))),
            p.Menu.SEPARATOR,
            p.MenuItem("Quit", lambda: self.root.after(0, self.quit)),
        )
        self.tray = p.Icon("rgb_scheduler", self.icon_img.resize((64, 64)), APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_toggle(self):
        self.enabled_var.set(not self.cfg["enabled"])
        self._toggle_enabled()

    def _on_unmap(self, event):
        if event.widget is self.root and self.root.state() == "iconic":
            self.root.after(10, self.root.withdraw)

    def hide_to_tray(self):
        """X just hides the window. The schedule keeps running; Quit from the tray stops it."""
        self.root.withdraw()
        if not self.cfg.get("_told_tray"):
            self.cfg["_told_tray"] = True
            save_config(self.cfg)
            try:
                self.tray.notify("Still running in the tray (^ by the clock). Right-click it and pick Quit to stop.",
                                 APP_NAME)
            except Exception:
                pass

    def _listen_for_show(self):
        """When the app is opened again while it's already running, show this window."""
        kernel32.CreateEventW.restype = wintypes.HANDLE
        ev = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        if not ev:
            return
        while not self.quitting:
            if kernel32.WaitForSingleObject(ev, 1000) == 0:
                self.root.after(0, self.show)

    def show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.refresh()

    def quit(self):
        self.quitting = True
        if not self.cfg.get("close_after", False):
            try:
                kill_asr(self.cfg)          # don't leave an invisible Polychrome behind
            except Exception:
                pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ================================================================ main

def main():
    if "--make-icon" in sys.argv:
        make_icon_image(256).save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                     (64, 64), (128, 128), (256, 256)])
        return

    if "--selftest" in sys.argv:
        selftest()
        return

    if not is_admin():
        # Open as admin through the saved task (no Yes/No prompt). Only the very
        # first time, before that task exists, Windows asks once.
        if open_through_task() or relaunch_as_admin():
            return
        user32.MessageBoxW(None, "RGB Scheduler needs admin rights to control ASRRGBLED.", APP_NAME, 0x10)
        return

    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.CreateMutexW(None, False, "Local\\RGBSchedulerSingleInstance")
    if kernel32.GetLastError() == 183:
        # already running (maybe hidden in the tray): just bring that window up
        ev = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)      # EVENT_MODIFY_STATE
        if ev:
            kernel32.SetEvent(ev)
            kernel32.CloseHandle(ev)
        else:
            user32.MessageBoxW(None, "RGB Scheduler is already running. Check the tray (^ by the clock).",
                               APP_NAME, 0x40)
        return

    try:
        ensure_launch_task()      # keeps future opens prompt-free, even if the app was moved
    except Exception:
        pass

    App(start_minimized="--minimized" in sys.argv).run()


def selftest():
    import traceback
    try:
        app = App(start_minimized=False, selftest=True)
        app.run()
        errs = getattr(app, "selftest_errors", ["didn't finish"])
    except Exception:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(os.path.join(CONFIG_DIR, "selftest.txt"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            if sys.stderr:
                traceback.print_exc()
        except Exception:
            pass
        sys.exit(1)
    lines = [f"SELFTEST ERROR: {e!r}" for e in errs] + [f"selftest {'FAILED' if errs else 'passed'}"]
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        if sys.stdout:
            print("\n".join(lines))
    except Exception:
        pass
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
