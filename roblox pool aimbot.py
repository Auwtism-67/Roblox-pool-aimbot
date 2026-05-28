# this makes lines on the screen to help aim in roblox pool
# movies watched in background while making this; 3 cars 2 madagasgar and jumanji(the good one)
import sys
import time
import ctypes
import ctypes.wintypes
import math
import cv2
import numpy as np

try:
    import win32gui
    import win32con
    import win32api
except ImportError:
    print("you need to do: pip install pywin32")
    print("just trust me")
    sys.exit(1)

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# these are percentages of the window size so it works on different resolutions hopefully
TABLE_LEFT   = 0.158
TABLE_TOP    = 0.175
TABLE_RIGHT  = 0.857
TABLE_BOTTOM = 0.895

# settings for finding the white ball
CUE_MIN_R      = 10   # ball cant be smaller than this
CUE_MAX_R      = 22   # ball cant be bigger than this
CUE_MIN_BRIGHT = 210  # IF ITS BRIGHT ITS WHITE
CUE_MAX_SAT    = 18   # erm akstully white doesnt have much saturation 

# line detection stuff
WHITE_THRESH    = 210
HOUGH_MINLEN    = 25  # ignore super short lines
HOUGH_MAXGAP    = 6   # how much a line can have gaps in it
HOUGH_THRESHOLD = 8   # lower by 2s or else it glitches :c no im not fixing it

# how close a second ball has to be to show the red line
SECONDARY_SNAP_DIST = 75

# target fps
FPS = 120


# i copied this struct stuff from stackoverflow 
# something about telling windows how big our image is
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


# this function takes a screenshot of just the roblox window
# printwindow is the magic function apparently abracadabra
def capture_window(hwnd, w, h):
    hdc_win = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp    = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old     = gdi32.SelectObject(hdc_mem, hbmp)

    user32.PrintWindow(hwnd, hdc_mem, 0x2)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize        = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth       = w
    bmi.bmiHeader.biHeight      = -h  # negative means top-down, dont ask me why
    bmi.bmiHeader.biPlanes      = 1
    bmi.bmiHeader.biBitCount    = 32
    bmi.bmiHeader.biCompression = 0

    buf   = (ctypes.c_uint8 * (w * h * 4))()
    lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h,
                             ctypes.byref(buf), ctypes.byref(bmi), 0)

    # cleanup so we dont leak memory i think
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)

    if not lines:
        return None

    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
    return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)


# find the roblox window by looking for "roblox" in the title
# returns the biggest one in case there's multiple for some reason
def find_roblox():
    candidates = []

    def check_window(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).lower()
        if "roblox" in title:
            try:
                rect = win32gui.GetClientRect(hwnd)
                width  = rect[2]
                height = rect[3]
                if width > 400 and height > 300:
                    area = width * height
                    candidates.append((area, hwnd, win32gui.GetWindowText(hwnd)))
            except:
                pass  # sometimes it just fails idk

    win32gui.EnumWindows(check_window, None)

    if not candidates:
        return None, None

    candidates.sort(reverse=True)  # biggest window first
    return candidates[0][1], candidates[0][2]


# find the white cue ball using circle detection
def find_cue(bgr):
    hsv  = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = hsv[:, :, 2]  # just the brightness channel
    blur = cv2.GaussianBlur(gray, (9, 9), 2)  # blur it a bit so detection works better
    H, W = gray.shape

    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=20,
        param1=50,
        param2=20,
        minRadius=CUE_MIN_R,
        maxRadius=CUE_MAX_R,
    )

    if circles is None:
        return None  # nothing found, go home and watch toy story

    best       = None
    best_score = -1

    for cx, cy, r in np.round(circles[0]).astype(int):
        # skip circles that are touching the edge
        if not (r < cx < W - r and r < cy < H - r):
            continue

        # sample the colors inside the circle
        mask = np.zeros((H, W), np.uint8)
        cv2.circle(mask, (cx, cy), max(r - 2, 1), 255, -1)

        mean_brightness = cv2.mean(hsv[:, :, 2], mask=mask)[0]
        mean_saturation = cv2.mean(hsv[:, :, 1], mask=mask)[0]

        # cue ball is bright AND not colorful (white = low saturation)
        if mean_brightness >= CUE_MIN_BRIGHT and mean_saturation <= CUE_MAX_SAT:
            score = mean_brightness - mean_saturation  # higher is whiter i think
            if score > best_score:
                best_score = score
                best = (cx, cy, int(r))

    return best


# make a mask of just the white pixels on the table
# i use this to detect the cue lines
def build_white_mask(bgr, cue):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # find all the white-ish pixels
    white_mask = cv2.inRange(
        hsv,
        np.array([0,   0,   WHITE_THRESH]),
        np.array([180, 40,  255])
    )

    # remove the green felt so we dont detect that
    felt_mask = cv2.inRange(hsv, np.array([35, 30, 30]), np.array([95, 255, 255]))
    white_mask = cv2.bitwise_and(white_mask, cv2.bitwise_not(felt_mask))

    # erase the cue ball itself so it doesnt mess up line detection
    if cue:
        cv2.circle(white_mask, cue[:2], cue[2] + 8, 0, -1)

    # clean up noise
    kernel = np.ones((3, 3), np.uint8)
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN,  kernel)
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)
    white_mask = cv2.erode(white_mask, np.ones((2, 2), np.uint8), iterations=1)

    return white_mask


# use hough lines to find straight lines in the mask
def hough_segments(mask):
    segments = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 180,
        threshold=HOUGH_THRESHOLD,
        minLineLength=HOUGH_MINLEN,
        maxLineGap=HOUGH_MAXGAP,
    )
    if segments is None:
        return []
    return [tuple(x[0]) for x in segments]

# helpers
# 1 get angle of a line segment (0 to 180 degrees)
def seg_angle(x1, y1, x2, y2):
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180


# 2 get length of a line segment
def seg_length(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


# 3 how far is a point from a line segment
def pt_to_seg_dist(px, py, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


# merge lines that are pointing the same direction into one line
# otherwise we get like 20 lines for the same cue stick
def merge_collinear(segs, angle_tol=12, dist_tol=15):
    if not segs:
        return []

    buckets = []
    for seg in segs:
        angle = seg_angle(*seg)
        placed = False
        for bucket in buckets:
            diff = abs(bucket[0] - angle)
            if diff < angle_tol or abs(diff - 180) < angle_tol:
                bucket[1].append(seg)
                placed = True
                break
        if not placed:
            buckets.append([angle, [seg]])

    result = []
    for _, group in buckets:
        # keep the longest one from each group
        group.sort(key=lambda s: seg_length(*s), reverse=True)
        result.append(group[0])

    return result


# figure out which end of the segment is closer to the cue ball
# returns (near_end, far_end)
def oriented_toward(seg, cue):
    x1, y1, x2, y2 = seg
    cx, cy = cue[:2]
    d1 = math.hypot(cx - x1, cy - y1)
    d2 = math.hypot(cx - x2, cy - y2)
    if abs(d1 - d2) < 10:
        return (x1, y1), (x2, y2)
    if d1 < d2:
        return (x1, y1), (x2, y2)
    else:
        return (x2, y2), (x1, y1)


# extend a line from one point through another all the way to the screen edge
def extend_from_origin(origin, target, W, H):
    ox, oy = origin
    tx, ty = target
    dx = tx - ox
    dy = ty - oy
    length = math.hypot(dx, dy)
    if length == 0:
        return ox, oy, tx, ty

    # normalize direction
    dx /= length
    dy /= length

    # find where we hit the screen border
    ts = []
    if dx > 0: ts.append((W - 1 - ox) / dx)
    if dx < 0: ts.append(-ox / dx)
    if dy > 0: ts.append((H - 1 - oy) / dy)
    if dy < 0: ts.append(-oy / dy)

    t = min((v for v in ts if v >= 0), default=0)

    end_x = int(ox + dx * t)
    end_y = int(oy + dy * t)
    return ox, oy, end_x, end_y


# figure out which line is the cue line (primary) and which is where it hits (secondary)
def find_primary_secondary(segs, cue):
    if not segs or cue is None:
        return None, None

    cx, cy = cue[:2]

    # closest line to the cue ball is probably the cue stick
    ranked = sorted(segs, key=lambda s: pt_to_seg_dist(cx, cy, *s))
    primary = ranked[0]

    # where does the primary line end (the impact point)
    _, impact = oriented_toward(primary, cue)
    ix, iy = impact

    primary_angle = seg_angle(*primary)
    secondary     = None
    best_dist     = SECONDARY_SNAP_DIST

    # look for another line near the impact point (the ball we're hitting)
    for seg in ranked[1:]:
        x1, y1, x2, y2 = seg

        d1 = math.hypot(x1 - ix, y1 - iy)
        d2 = math.hypot(x2 - ix, y2 - iy)
        nearest = min(d1, d2)

        if nearest > best_dist:
            continue

        # skip if its basically the same angle as the primary line
        seg_ang   = seg_angle(*seg)
        angle_diff = abs(primary_angle - seg_ang)
        angle_diff = min(angle_diff, 180 - angle_diff)
        if angle_diff < 1:
            continue

        best_dist = nearest
        secondary = seg

    return primary, secondary


# create the transparent overlay window that sits on top of roblox
def create_overlay():
    hInst = win32api.GetModuleHandle(None)
    class_name = "PoolOverlayV5"

    wc = win32gui.WNDCLASS()
    wc.hInstance     = hInst
    wc.lpszClassName = class_name
    wc.lpfnWndProc   = win32gui.DefWindowProc
    wc.hbrBackground = 0

    try:
        win32gui.RegisterClass(wc)
    except:
        pass  # already registered, thats fine

    # these flags make the window transparent and click-through
    ex_style = (
        win32con.WS_EX_TOPMOST    |
        win32con.WS_EX_LAYERED    |
        win32con.WS_EX_TRANSPARENT|
        win32con.WS_EX_NOACTIVATE
    )

    hwnd = win32gui.CreateWindowEx(
        ex_style, class_name, "", win32con.WS_POPUP,
        0, 0, 100, 100,
        None, None, hInst, None
    )

    # make black = transparent
    ctypes.windll.user32.SetLayeredWindowAttributes(
        hwnd, win32api.RGB(0, 0, 0), 0, win32con.LWA_COLORKEY
    )
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    return hwnd


# actually draw the lines onto the overlay
def paint(hwnd, W, H, cue, primary_seg, secondary_seg):
    SRCCOPY = 0xCC0020

    hdc_win = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp    = gdi32.CreateCompatibleBitmap(hdc_win, W, H)
    old     = gdi32.SelectObject(hdc_mem, hbmp)

    # fill with black (which is our transparent color)
    brush = gdi32.CreateSolidBrush(0)
    rc    = ctypes.wintypes.RECT(0, 0, W, H)
    user32.FillRect(hdc_mem, ctypes.byref(rc), brush)
    gdi32.DeleteObject(brush)

    # helper to draw a line with a color
    def draw_line(x1, y1, x2, y2, colorref, thickness=3):
        pen = gdi32.CreatePen(0, thickness, colorref)
        old_pen = gdi32.SelectObject(hdc_mem, pen)
        gdi32.MoveToEx(hdc_mem, int(x1), int(y1), None)
        gdi32.LineTo(hdc_mem, int(x2), int(y2))
        gdi32.SelectObject(hdc_mem, old_pen)
        gdi32.DeleteObject(pen)

    # windows wants BGR order not RGB, annoying
    def make_color(r, g, b):
        return (b << 16) | (g << 8) | r

    # draw the blue aim line (main trajectory)
    if primary_seg and cue:
        origin, _ = oriented_toward(primary_seg, cue)
        _, (tx, ty) = oriented_toward(primary_seg, cue)
        ox, oy, ex, ey = extend_from_origin(origin, (tx, ty), W, H)
        draw_line(ox, oy, ex, ey, make_color(0, 140, 255), 3)

    # draw the red deflection line (where the other ball goes)
    if secondary_seg and cue:
        _, impact = oriented_toward(primary_seg, cue)
        ix, iy = impact
        x1, y1, x2, y2 = secondary_seg
        d1 = math.hypot(x1 - ix, y1 - iy)
        d2 = math.hypot(x2 - ix, y2 - iy)
        if d1 < d2:
            sec_origin = (x1, y1)
            sec_target = (x2, y2)
        else:
            sec_origin = (x2, y2)
            sec_target = (x1, y1)
        ox, oy, ex, ey = extend_from_origin(sec_origin, sec_target, W, H)
        draw_line(ox, oy, ex, ey, make_color(220, 30, 30), 3)

    # draw a green circle around the cue ball so we know its being tracked
    if cue:
        cx, cy, r = cue
        num_segments = 60
        for i in range(num_segments):
            a1 = math.radians(i       * 360 / num_segments)
            a2 = math.radians((i + 1) * 360 / num_segments)
            draw_line(
                cx + r * math.cos(a1), cy + r * math.sin(a1),
                cx + r * math.cos(a2), cy + r * math.sin(a2),
                make_color(0, 220, 0), 2
            )

    # copy to screen
    gdi32.BitBlt(hdc_win, 0, 0, W, H, hdc_mem, 0, 0, SRCCOPY)
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)


def main():
    print("=" * 50)
    print("  roblox pool aim helper  (press ctrl+c to stop)")
    print("=" * 50)
    print()

    hwnd, title = find_roblox()
    if not hwnd:
        print("couldnt find roblox!! make sure its open")
        sys.exit(1)

    print(f"found it: \"{title}\"")
    print("running... press ctrl+c to quit\n")

    overlay = create_overlay()
    frame_time = 1.0 / FPS
    error_count = 0

    try:
        while True:
            start = time.perf_counter()

            # get window position and size
            try:
                client_rect = win32gui.GetClientRect(hwnd)
                cw = client_rect[2]
                ch = client_rect[3]
                ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
            except:
                time.sleep(0.5)
                hwnd, _ = find_roblox()
                continue

            if cw < 100 or ch < 100:
                time.sleep(0.1)
                continue

            # grab a screenshot of the window
            frame = capture_window(hwnd, cw, ch)
            if frame is None:
                error_count += 1
                if error_count % 60 == 0:
                    print(f"\nwarning: screenshot failed {error_count} times")
                time.sleep(0.05)
                continue
            error_count = 0

            # crop to just the table area
            x0 = int(TABLE_LEFT   * cw)
            x1 = int(TABLE_RIGHT  * cw)
            y0 = int(TABLE_TOP    * ch)
            y1 = int(TABLE_BOTTOM * ch)
            table = frame[y0:y1, x0:x1]
            tH, tW = table.shape[:2]

            if tW < 10 or tH < 10:
                time.sleep(0.1)
                continue

            # do all the detection stuff
            cue          = find_cue(table)
            mask         = build_white_mask(table, cue)
            raw_segs     = hough_segments(mask)
            merged       = merge_collinear(raw_segs)
            primary, sec = find_primary_secondary(merged, cue)

            # move overlay window to match the table
            win32gui.SetWindowPos(
                overlay, win32con.HWND_TOPMOST,
                ox + x0, oy + y0, tW, tH,
                win32con.SWP_NOACTIVATE
            )
            paint(overlay, tW, tH, cue, primary, sec)
            win32gui.PumpWaitingMessages()

            # print some debug info
            cue_str = f"({cue[0]},{cue[1]} r={cue[2]})" if cue else "not found     "
            sys.stdout.write(
                f"\r  ball:{cue_str:<22} lines:{len(merged)}"
                f"  blue:{'yes' if primary else 'no '}"
                f"  red:{'yes' if sec else 'no '}"
                f"  table:{tW}x{tH}  "
            )
            sys.stdout.flush()

            # sleep for the rest of the frame
            elapsed = time.perf_counter() - start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nstopped! bye")
    finally:
        try:
            win32gui.DestroyWindow(overlay)
        except:
            pass


if __name__ == "__main__":
    main()
