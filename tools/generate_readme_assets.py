"""Regenerate GitPulse's README banner, previews and tutorial GIF."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

BG = "#07100C"
PANEL = "#0C1C15"
PANEL_2 = "#10251B"
BORDER = "#1E4935"
TEXT = "#F4FAF6"
MUTED = "#8DA99A"
SUBTLE = "#5F7D6D"
ACCENT = "#27F58A"
ACCENT_DARK = "#0B4329"
RED = "#FF7B83"

FONT_CANDIDATES = {
    "regular": [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ],
    "bold": [
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ],
    "mono": [
        Path("C:/Windows/Fonts/CascadiaMono.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ],
}


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    family = "mono" if mono else "bold" if bold else "regular"
    path = next((candidate for candidate in FONT_CANDIDATES[family] if candidate.exists()), None)
    if path is None:
        raise FileNotFoundError(f"No supported {family} font was found on this system.")
    return ImageFont.truetype(str(path), size)


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    ratio = max(size[0] / image.width, size[1] / image.height)
    scaled = image.resize((round(image.width * ratio), round(image.height * ratio)), Image.Resampling.LANCZOS)
    left = (scaled.width - size[0]) // 2
    top = (scaled.height - size[1]) // 2
    return scaled.crop((left, top, left + size[0], top + size[1]))


def label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, color: str = TEXT, bold: bool = False, mono: bool = False, anchor: str | None = None) -> None:
    draw.text(xy, text, font=font(size, bold=bold, mono=mono), fill=color, anchor=anchor)


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str = PANEL, outline: str = BORDER, radius: int = 18, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def button(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, primary: bool = False, danger: bool = False) -> None:
    if primary:
        fill, ink, outline = ACCENT, BG, ACCENT
    elif danger:
        fill, ink, outline = "#3A191F", "#FFD8DB", "#6B3038"
    else:
        fill, ink, outline = PANEL_2, TEXT, BORDER
    draw.rounded_rectangle(box, radius=11, fill=fill, outline=outline, width=2)
    label(draw, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), text, 15, ink, bold=True, anchor="mm")


def generate_banner() -> None:
    background = fit_cover(Image.open(ASSETS / "gitpulse-banner-background.png").convert("RGB"), (1600, 600)).convert("RGBA")
    shade = Image.new("RGBA", background.size, (2, 11, 7, 105))
    background.alpha_composite(shade)
    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((70, 66, 1530, 534), radius=34, fill=(3, 15, 10, 155), outline=(39, 245, 138, 95), width=2)

    logo = Image.open(ASSETS / "gitpulse-logo.png").convert("RGBA")
    logo.thumbnail((310, 310), Image.Resampling.LANCZOS)
    glow = Image.new("RGBA", background.size, (0, 0, 0, 0))
    glow.paste(logo, (122, 145), logo)
    alpha = glow.getchannel("A").filter(ImageFilter.GaussianBlur(28))
    green_glow = Image.new("RGBA", background.size, (39, 245, 138, 0))
    green_glow.putalpha(alpha.point(lambda value: int(value * 0.45)))
    background.alpha_composite(green_glow)
    background.alpha_composite(glow)

    draw = ImageDraw.Draw(background)
    draw.line((505, 137, 505, 463), fill=(39, 245, 138, 90), width=2)
    label(draw, (568, 170), "GITPULSE", 22, ACCENT, bold=True)
    label(draw, (565, 213), "GitPulse", 74, TEXT, bold=True)
    label(draw, (570, 310), "Life support for GitHub.", 33, "#CDE0D5")
    label(draw, (572, 374), "Scheduled, visible, non-empty Git commits — from one calm command center.", 19, MUTED)
    for index, text in enumerate(("MULTI-REPO", "BACKGROUND WORKER", "SAFE PULSES")):
        x = 570 + (0, 174, 413)[index]
        widths = (148, 216, 163)
        draw.rounded_rectangle((x, 421, x + widths[index], 463), radius=12, fill=(11, 67, 41, 175), outline=(39, 245, 138, 140), width=1)
        label(draw, (x + widths[index] // 2, 442), text, 13, "#C7F9DD", bold=True, anchor="mm")
    background.convert("RGB").save(ASSETS / "gitpulse-banner.png", quality=96)


def dashboard(step: int = 0) -> Image.Image:
    image = Image.new("RGB", (1500, 900), BG)
    draw = ImageDraw.Draw(image)

    logo = Image.open(ASSETS / "gitpulse-logo.png").convert("RGBA")
    logo.thumbnail((64, 64), Image.Resampling.LANCZOS)
    image.paste(logo, (38, 27), logo)
    label(draw, (116, 31), "GitPulse", 30, TEXT, bold=True)
    label(draw, (118, 69), "Life support for GitHub.", 15, MUTED)
    label(draw, (1235, 47), "v1.5.0", 13, SUBTLE, mono=True, anchor="mm")
    draw.rounded_rectangle((1265, 25, 1438, 69), radius=14, fill=ACCENT_DARK, outline=BORDER)
    label(draw, (1351, 47), "Worker online", 14, ACCENT, bold=True, anchor="mm")

    card(draw, (34, 112, 372, 870), fill="#091711", outline="#163A2A", radius=22)
    label(draw, (58, 144), "Repositories", 23, TEXT, bold=True)
    button(draw, (272, 134, 346, 176), "+ Add", primary=True)
    label(draw, (58, 185), "2 active  ·  16/28 pulses", 13, MUTED)

    def repo(y: int, name: str, progress: str, width: int, selected: bool) -> None:
        card(draw, (50, y, 354, y + 98), fill=ACCENT_DARK if selected else PANEL, outline=ACCENT if selected else "#163A2A", radius=15)
        label(draw, (70, y + 21), name, 17, TEXT, bold=True)
        label(draw, (70, y + 50), progress, 12, MUTED)
        draw.ellipse((324, y + 24, 334, y + 34), fill=ACCENT)
        draw.rounded_rectangle((70, y + 76, 334, y + 82), radius=3, fill="#17372A")
        draw.rounded_rectangle((70, y + 76, 70 + width, y + 82), radius=3, fill=ACCENT)

    repo(216, "GitPulse", "11/20 today   ·   5:59 PM next   ·   pulse span", 145, True)
    repo(326, "IceBreaker", "5/8 today   ·   7:39 PM next", 165, False)
    card(draw, (50, 650, 354, 850), fill=PANEL, outline="#163A2A", radius=17)
    label(draw, (70, 672), "AUTOMATION", 11, SUBTLE, bold=True)
    label(draw, (70, 698), "Background life support", 16, TEXT, bold=True)
    label(draw, (70, 731), "☑  Start in background with Windows", 12, MUTED)
    button(draw, (68, 758, 223, 804), "Start", primary=True)
    button(draw, (232, 758, 336, 804), "Stop", danger=True)
    button(draw, (68, 814, 336, 842), "Complete all repos now")

    label(draw, (405, 119), "Command center", 29, TEXT, bold=True)
    label(draw, (407, 160), "Safe contribution pulses and local-project sync in one place.", 15, MUTED)
    button(draw, (1350, 127, 1438, 169), "Refresh")
    card(draw, (404, 192, 1466, 534), fill=PANEL, outline="#163A2A", radius=22)

    draw.ellipse((440, 232, 610, 402), outline="#17372A", width=14)
    draw.arc((440, 232, 610, 402), start=-90, end=108, fill=ACCENT, width=14)
    label(draw, (525, 305), "11/20", 31, TEXT, bold=True, anchor="mm")
    label(draw, (525, 340), "PULSES TODAY", 11, MUTED, bold=True, anchor="mm")
    label(draw, (649, 226), "GitPulse", 27, TEXT, bold=True)
    label(draw, (649, 263), "github.com/anamta-JINX/GitPulse-LifeSupportForGitHub", 13, MUTED)
    draw.rounded_rectangle((1334, 220, 1432, 256), radius=10, fill=ACCENT_DARK, outline=BORDER)
    label(draw, (1383, 238), "ACTIVE", 11, ACCENT, bold=True, anchor="mm")
    for x, title, value, color in ((649, "NEXT PULSE", "5:59 PM", ACCENT), (842, "TIME WINDOW", "10:00 AM — 11:59 PM", TEXT), (1035, "BRANCH", "Default", TEXT), (1228, "HOURLY SYNC", "No staged changes", "#42E8D0")):
        card(draw, (x, 291, x + 180, 382), fill=PANEL_2, outline="#163A2A", radius=13)
        label(draw, (x + 17, 312), title, 11, SUBTLE, bold=True)
        value_size = 12 if title == "TIME WINDOW" else 13 if title == "HOURLY SYNC" else 15
        label(draw, (x + 17, 344), value, value_size, color, bold=True)
    button(draw, (441, 445, 552, 494), "Pulse now", primary=True)
    button(draw, (562, 445, 678, 494), "Hourly sync")
    button(draw, (688, 445, 795, 494), "Pulse span")
    button(draw, (805, 445, 945, 494), "Complete today")
    button(draw, (955, 445, 1080, 494), "Open GitHub")
    button(draw, (1090, 445, 1170, 494), "Edit")
    button(draw, (1180, 445, 1278, 494), "Remove", danger=True)

    card(draw, (404, 552, 1466, 870), fill=PANEL, outline="#163A2A", radius=22)
    label(draw, (430, 579), "Recent activity", 21, TEXT, bold=True)
    label(draw, (1435, 583), "Double-click a row for details", 12, SUBTLE, anchor="ra")
    headers = ((430, "TIME"), (655, "REPOSITORY"), (1000, "PULSE"), (1145, "COMMIT"), (1335, "STATUS"))
    draw.rounded_rectangle((426, 615, 1440, 656), radius=9, fill=PANEL_2)
    for x, text in headers:
        label(draw, (x, 628), text, 11, MUTED, bold=True)
    rows = [
        ("Aug 13  8:41 PM", "GitPulse", "11/20", "a91c42d7f0", "Pushed"),
        ("Aug 13  8:06 PM", "IceBreaker", "5/8", "76b1a0358d", "Pushed"),
        ("Aug 13  7:52 PM", "GitPulse", "10/20", "35b07f55ce", "Pushed"),
        ("Aug 13  7:17 PM", "GitPulse", "9/20", "8f66d4f731", "Pushed"),
    ]
    for index, row in enumerate(rows):
        y = 680 + index * 42
        for (x, _), value in zip(headers, row):
            label(draw, (x, y), value, 13, ACCENT if value == "Pushed" else TEXT, mono=value.startswith(tuple("0123456789abcdef")) and len(value) == 10)

    if step:
        caption = {
            1: "1  Connect a repository",
            2: "2  Set its contribution schedule",
            3: "3  Start the background worker",
            4: "4  Watch each real commit land in the activity feed",
        }[step]
        draw.rounded_rectangle((410, 30, 1170, 90), radius=18, fill="#06150EA8", outline=ACCENT, width=3)
        label(draw, (790, 60), caption, 19, TEXT, bold=True, anchor="mm")
        targets = {1: (265, 127, 353, 184), 2: (410, 191, 1465, 535), 3: (62, 751, 230, 811), 4: (402, 550, 1467, 871)}
        draw.rounded_rectangle(targets[step], radius=18, outline=ACCENT, width=5)
    return image


def setup_preview(editing: bool = False) -> Image.Image:
    base = dashboard()
    veil = Image.new("RGBA", base.size, (0, 0, 0, 145))
    image = Image.alpha_composite(base.convert("RGBA"), veil)
    draw = ImageDraw.Draw(image)
    card(draw, (280, 70, 1220, 830), fill="#08130E", outline=BORDER, radius=24, width=2)
    label(draw, (325, 105), "REPOSITORY CONNECTION", 12, ACCENT, bold=True)
    label(draw, (325, 134), "Edit repository" if editing else "Connect a repository", 29, TEXT, bold=True)
    label(draw, (325, 174), "Connect GitHub and choose the contribution-pulse schedule.", 14, MUTED)

    fields = [
        ("Display name", "Portfolio", 212),
        ("GitHub repository URL", "https://github.com/username/portfolio", 302),
        ("Commit email", "you@example.com", 392),
    ]
    for title, value, y in fields:
        label(draw, (325, y), title, 12, TEXT, bold=True)
        draw.rounded_rectangle((325, y + 22, 1175, y + 63), radius=10, fill=PANEL_2, outline=BORDER)
        label(draw, (341, y + 35), value, 14, TEXT)
    for x, title, value in ((325, "Pulses / day", "12"), (615, "Start time", "10:00 AM"), (905, "End time", "11:30 PM")):
        label(draw, (x, 494), title, 12, TEXT, bold=True)
        if title == "Pulses / day":
            draw.rounded_rectangle((x, 516, x + 270, 557), radius=10, fill=PANEL_2, outline=BORDER)
            label(draw, (x + 16, 529), value, 14, TEXT)
        else:
            hour, remainder = value.split(":", 1)
            minute, period = remainder.split()
            draw.rounded_rectangle((x, 516, x + 74, 557), radius=10, fill=PANEL_2, outline=BORDER)
            draw.rounded_rectangle((x + 92, 516, x + 171, 557), radius=10, fill=PANEL_2, outline=BORDER)
            draw.rounded_rectangle((x + 181, 516, x + 270, 557), radius=10, fill=PANEL_2, outline=BORDER)
            label(draw, (x + 14, 529), f"{hour}  ▾", 13, TEXT, bold=True)
            label(draw, (x + 80, 529), ":", 14, MUTED, bold=True)
            label(draw, (x + 105, 529), f"{minute}  ▾", 13, TEXT, bold=True)
            label(draw, (x + 194, 529), f"{period}  ▾", 13, TEXT, bold=True)
    label(draw, (325, 584), "Branch (optional)", 12, TEXT, bold=True)
    draw.rounded_rectangle((325, 606, 1175, 647), radius=10, fill=PANEL_2, outline=BORDER)
    label(draw, (341, 619), "Leave blank for repository default", 13, SUBTLE)
    label(draw, (325, 682), "☑  Enable scheduled pulses", 13, TEXT)
    button(draw, (325, 746, 490, 789), "Test connection")
    button(draw, (975, 746, 1175, 789), "Save changes" if editing else "Add repository", primary=True)
    return image.convert("RGB")


def hourly_sync_preview() -> Image.Image:
    base = dashboard()
    veil = Image.new("RGBA", base.size, (0, 0, 0, 155))
    image = Image.alpha_composite(base.convert("RGBA"), veil)
    draw = ImageDraw.Draw(image)
    card(draw, (410, 95, 1090, 805), fill="#08130E", outline=BORDER, radius=24, width=2)
    label(draw, (452, 132), "HOURLY SYNC", 12, "#42E8D0", bold=True)
    label(draw, (452, 166), "Your staged work, pushed automatically.", 24, TEXT, bold=True)
    label(draw, (452, 208), "Save once. GitPulse checks now, then every 60 minutes.", 14, MUTED)
    card(draw, (452, 248, 1048, 332), fill=PANEL, outline=BORDER, radius=13)
    label(draw, (472, 270), "Turn on Hourly Sync", 16, TEXT, bold=True)
    label(draw, (472, 299), "The background worker starts automatically.", 12, MUTED)
    draw.rounded_rectangle((970, 269, 1026, 309), radius=20, fill=ACCENT, outline=ACCENT)
    draw.ellipse((994, 273, 1022, 305), fill=BG)
    label(draw, (452, 365), "Local repository folder", 13, TEXT, bold=True)
    draw.rounded_rectangle((452, 392, 935, 442), radius=10, fill=PANEL_2, outline=BORDER)
    label(draw, (470, 408), r"D:\Nerd Stuff\Projects\Portfolio", 13, TEXT)
    button(draw, (946, 392, 1048, 442), "Browse")
    card(draw, (452, 477, 1048, 678), fill=PANEL, outline=BORDER, radius=13)
    label(draw, (472, 499), "HOW IT WORKS", 11, SUBTLE, bold=True)
    for index, title in enumerate(("Stage files normally with git add", "GitPulse checks every 60 minutes", "Staged work is committed and pushed"), start=1):
        y = 535 + (index - 1) * 35
        draw.rounded_rectangle((472, y, 502, y + 27), radius=8, fill=ACCENT_DARK)
        label(draw, (487, y + 14), str(index), 11, ACCENT, bold=True, anchor="mm")
        label(draw, (518, y + 5), title, 13, TEXT)
    label(draw, (472, 648), "Only your staged snapshot is committed; all other work is restored.", 12, "#42E8D0", bold=True)
    button(draw, (452, 721, 557, 767), "Cancel")
    button(draw, (856, 721, 1048, 767), "Save & check now", primary=True)
    return image.convert("RGB")


def calendar_preview() -> Image.Image:
    base = dashboard()
    veil = Image.new("RGBA", base.size, (0, 0, 0, 160))
    image = Image.alpha_composite(base.convert("RGBA"), veil)
    draw = ImageDraw.Draw(image)
    card(draw, (180, 52, 1320, 848), fill="#08130E", outline=BORDER, radius=24, width=2)
    label(draw, (225, 88), "AUTOMATIC PULSE SPAN", 12, ACCENT, bold=True)
    label(draw, (225, 120), "Plan the full span, not each day", 28, TEXT, bold=True)
    label(draw, (225, 162), "Set one pulse total. Every day gets at least one; GitPulse manages the rest.", 14, MUTED)

    card(draw, (225, 194, 1275, 347), fill=PANEL, outline=BORDER, radius=14)
    first_row = ((245, "START DATE", "2026-08-14"), (515, "DAYS IN SPAN", "30"), (705, "PULSES FOR FULL SPAN", "174"))
    for x, title, value in first_row:
        label(draw, (x, 211), title, 10, SUBTLE, bold=True)
        label(draw, (x, 239), value, 14, TEXT, bold=True)
    second_row = ((245, "START TIME", "10  :  00   AM  ▾"), (765, "END TIME", "5  :  00   PM  ▾"))
    for x, title, value in second_row:
        label(draw, (x, 274), title, 10, SUBTLE, bold=True)
        draw.rounded_rectangle((x, 296, x + 465, 330), radius=8, fill=PANEL_2, outline=BORDER)
        label(draw, (x + 15, 303), value, 13, TEXT, bold=True)

    card(draw, (225, 365, 1010, 747), fill=PANEL, outline=BORDER, radius=14)
    label(draw, (247, 386), "174 SPAN PULSES  ·  30 DAYS  ·  3–9 PER DAY", 10, ACCENT, bold=True)
    for column, weekday in enumerate(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")):
        label(draw, (262 + column * 104, 416), weekday, 9, SUBTLE, bold=True)
    samples = [
        (14, 6, "10:18 AM"), (15, 9, "10:07 AM"), (16, 3, "10:31 AM"), (17, 7, "10:12 AM"),
        (18, 4, "10:25 AM"), (19, 8, "10:03 AM"), (20, 4, "10:42 AM"), (21, 6, "10:15 AM"),
        (22, 8, "10:36 AM"), (23, 5, "10:09 AM"), (24, 7, "10:27 AM"), (25, 3, "10:20 AM"),
        (26, 8, "10:45 AM"), (27, 4, "10:05 AM"), (28, 5, "10:33 AM"), (29, 7, "10:11 AM"),
        (30, 5, "10:39 AM"), (31, 8, "10:22 AM"), (1, 3, "10:02 AM"), (2, 8, "10:29 AM"),
        (3, 6, "10:17 AM"), (4, 4, "10:41 AM"), (5, 7, "10:08 AM"), (6, 5, "10:34 AM"),
        (7, 8, "10:14 AM"), (8, 3, "10:38 AM"), (9, 6, "10:06 AM"), (10, 7, "10:24 AM"),
        (11, 4, "10:43 AM"), (12, 6, "10:19 AM"),
    ]
    start_offset = 4
    for index, (day, pulse_count, first_time) in enumerate(samples):
        position = start_offset + index
        row, column = divmod(position, 7)
        x = 244 + column * 108
        y = 438 + row * 55
        selected = index == 0
        draw.rounded_rectangle((x, y, x + 98, y + 47), radius=9, fill=ACCENT_DARK if selected else PANEL_2, outline=ACCENT if selected else "#163A2A", width=2 if selected else 1)
        label(draw, (x + 8, y + 7), f"AUG {day}" if index < 18 else f"SEP {day}", 10, TEXT if selected else MUTED, bold=True)
        label(draw, (x + 8, y + 27), f"{pulse_count} pulses", 9, ACCENT if selected else SUBTLE)

    draw.rounded_rectangle((992, 432, 998, 728), radius=3, fill=PANEL_2)
    draw.rounded_rectangle((992, 437, 998, 570), radius=3, fill=MUTED)

    card(draw, (1028, 365, 1275, 747), fill=PANEL, outline=BORDER, radius=14)
    label(draw, (1050, 389), "DAY SCHEDULE", 10, SUBTLE, bold=True)
    label(draw, (1050, 421), "Friday", 19, TEXT, bold=True)
    label(draw, (1050, 450), "August 14, 2026", 13, MUTED)
    label(draw, (1050, 474), "6 pulses planned", 11, ACCENT, bold=True)
    for index, value in enumerate(("10:18 AM", "11:14 AM", "12:39 PM", "2:08 PM", "3:47 PM", "4:51 PM"), start=1):
        y = 507 + (index - 1) * 35
        label(draw, (1050, y), f"{index:02d}", 11, SUBTLE, mono=True)
        label(draw, (1095, y), value, 12, TEXT, mono=True)

    button(draw, (225, 775, 400, 821), "Generate span", primary=True)
    button(draw, (1110, 775, 1275, 821), "Save & start", primary=True)
    return image.convert("RGB")


def generate_tutorial() -> None:
    frames = [dashboard(step).resize((1200, 720), Image.Resampling.LANCZOS) for step in (1, 2, 3, 4)]
    palette = [frame.quantize(colors=192, method=Image.Quantize.MEDIANCUT) for frame in frames]
    options = dict(save_all=True, append_images=palette[1:], duration=[1600, 1900, 1600, 2100], loop=0, optimize=True, disposal=2)
    palette[0].save(ASSETS / "gitpulse-tutorial.gif", **options)
    palette[0].save(ASSETS / "gitpulse-demo.gif", **options)


def generate_workflow() -> None:
    image = Image.new("RGB", (1500, 620), BG)
    draw = ImageDraw.Draw(image)
    label(draw, (58, 42), "How GitPulse works", 36, TEXT, bold=True)
    label(draw, (60, 92), "Two modes. One explicit safety rule: GitPulse never stages your local work for you.", 16, MUTED)

    lanes = [
        ("AUTOMATIC PULSE SPAN", ACCENT, [
            ("Span total", "One budget for 1–30 days"),
            ("Daily allocation", "At least one pulse per day"),
            ("Commit + push", "Random count and time"),
            ("Tray notification", "Sent when the span completes"),
        ]),
        ("HOURLY LOCAL SYNC", "#42E8D0", [
            ("60-minute check", "Opt-in repository only"),
            ("Verify local repo", "Root, origin and branch"),
            ("Read staged index", "Never runs git add"),
            ("Commit + push", "Leave other work untouched"),
        ]),
    ]
    for lane_index, (lane_title, lane_color, steps) in enumerate(lanes):
        y = 168 + lane_index * 185
        label(draw, (58, y + 55), lane_title, 13, lane_color, bold=True)
        for index, (title, subtitle) in enumerate(steps):
            x = 245 + index * 300
            card(draw, (x, y, x + 260, y + 132), fill=PANEL, outline=BORDER, radius=17)
            draw.rounded_rectangle((x + 18, y + 18, x + 48, y + 48), radius=9, fill=lane_color)
            label(draw, (x + 33, y + 33), str(index + 1), 13, BG, bold=True, anchor="mm")
            label(draw, (x + 18, y + 67), title, 16, TEXT, bold=True)
            label(draw, (x + 18, y + 96), subtitle, 12, MUTED)
            if index < len(steps) - 1:
                draw.line((x + 265, y + 65, x + 292, y + 65), fill=lane_color, width=4)
                draw.polygon(((x + 292, y + 65), (x + 280, y + 57), (x + 280, y + 73)), fill=lane_color)
    draw.rounded_rectangle((382, 548, 1118, 596), radius=14, fill=ACCENT_DARK, outline=BORDER)
    label(draw, (750, 572), "Windows background startup  ·  Tray icon  ·  Completion notification", 14, ACCENT, bold=True, anchor="mm")
    image.save(ASSETS / "gitpulse-workflow.png", quality=95)


def main() -> None:
    generate_banner()
    dashboard().save(ASSETS / "gitpulse-ui.png", quality=95)
    setup_preview().save(ASSETS / "gitpulse-repository-setup.png", quality=95)
    setup_preview(editing=True).save(ASSETS / "gitpulse-repository-edit.png", quality=95)
    hourly_sync_preview().save(ASSETS / "gitpulse-hourly-sync.png", quality=95)
    calendar_preview().save(ASSETS / "gitpulse-calendar.png", quality=95)
    generate_workflow()
    generate_tutorial()


if __name__ == "__main__":
    main()
