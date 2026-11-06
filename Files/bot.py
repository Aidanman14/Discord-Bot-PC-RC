import asyncio
import ctypes
import datetime as dt
import io
import logging
import os
import platform
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import discord
import mss
import psutil
from discord import app_commands
from dotenv import load_dotenv
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
MAX_UPLOAD_BYTES = 9 * 1024 * 1024
KEEP_AWAKE = os.getenv("KEEP_AWAKE", "1").strip() != "0"
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
KEEP_SCREEN_ON = os.getenv("KEEP_SCREEN_ON", "1").strip() != "0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("pc-bot")

intents = discord.Intents.none()
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        log.warning("Blocked %s (%s) from /%s", interaction.user, interaction.user.id, interaction.command.name if interaction.command else "?")
        msg = "You're not allowed to use this bot."
    else:
        log.exception("Command failed", exc_info=error)
        msg = f"Something went wrong: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def bar(percent: float, width: int = 12) -> str:
    filled = round(percent / 100 * width)
    return "█" * filled + "░" * (width - filled)


def gpu_info() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except Exception:
        return []
    lines = []
    for row in out.splitlines():
        try:
            name, util, used, total, temp = [p.strip() for p in row.split(",")]
            lines.append(f"**{name}**\n`{bar(float(util))}` {util}% · VRAM {used}/{total} MB · {temp}°C")
        except ValueError:
            continue
    return lines


def collect_status() -> discord.Embed:
    cpu = psutil.cpu_percent(interval=1)
    freq = psutil.cpu_freq()
    ram = psutil.virtual_memory()
    uptime = time.time() - psutil.boot_time()

    embed = discord.Embed(
        title=f"🖥️ {platform.node()}",
        description=f"Online · up {fmt_duration(uptime)}",
        color=discord.Color.green(),
        timestamp=dt.datetime.now(dt.timezone.utc),
    )
    cpu_line = f"`{bar(cpu)}` {cpu:.0f}%"
    if freq:
        cpu_line += f" · {freq.current / 1000:.2f} GHz"
    embed.add_field(name="CPU", value=cpu_line, inline=False)
    embed.add_field(
        name="RAM",
        value=f"`{bar(ram.percent)}` {ram.percent:.0f}% · {fmt_bytes(ram.used)} / {fmt_bytes(ram.total)}",
        inline=False,
    )

    gpus = gpu_info()
    if gpus:
        embed.add_field(name="GPU", value="\n".join(gpus), inline=False)

    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(f"**{part.device}** `{bar(usage.percent)}` {usage.percent:.0f}% · {fmt_bytes(usage.free)} free")
    if disks:
        embed.add_field(name="Disks", value="\n".join(disks), inline=False)

    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery:
        plug = "charging" if battery.power_plugged else "on battery"
        embed.add_field(name="Battery", value=f"{battery.percent:.0f}% · {plug}", inline=False)

    return embed


def grab_screenshot(monitor: int) -> tuple[bytes, str, int]:
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        count = len(sct.monitors) - 1
        if monitor < 0 or monitor > count:
            raise ValueError(f"Monitor must be 0 (all) to {count}.")
        shot = sct.grab(sct.monitors[monitor])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= MAX_UPLOAD_BYTES:
        return buf.getvalue(), "screenshot.png", count

    for quality in (90, 75, 60, 45):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= MAX_UPLOAD_BYTES:
            return buf.getvalue(), "screenshot.jpg", count

    img.thumbnail((img.width // 2, img.height // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue(), "screenshot.jpg", count


@tree.command(name="status", description="Show whether the PC is online and how it's doing")
@app_commands.check(is_owner)
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    embed = await asyncio.to_thread(collect_status)
    await interaction.followup.send(embed=embed, ephemeral=True)
    log.info("/status by %s", interaction.user.id)


@tree.command(name="screenshot", description="Take a screenshot of the PC")
@app_commands.describe(monitor="0 = all screens, 1 = main screen, 2 = second screen...")
@app_commands.check(is_owner)
async def screenshot_cmd(interaction: discord.Interaction, monitor: app_commands.Range[int, 0, 9] = 1):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data, filename, count = await asyncio.to_thread(grab_screenshot, monitor)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return
    label = "all screens" if monitor == 0 else f"screen {monitor} of {count}"
    await interaction.followup.send(
        content=f"📸 {label} · <t:{int(time.time())}:T>",
        file=discord.File(io.BytesIO(data), filename=filename),
        ephemeral=True,
    )
    log.info("/screenshot monitor=%s by %s", monitor, interaction.user.id)


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APP_CACHE_SECONDS = 300
_app_cache: dict[str, str] = {}
_app_cache_time = 0.0

PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "secure system", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe",
    "memcompression", "msmpeng.exe",
}


def load_start_apps() -> dict[str, str]:
    global _app_cache, _app_cache_time
    if _app_cache and time.time() - _app_cache_time < APP_CACHE_SECONDS:
        return _app_cache
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, creationflags=NO_WINDOW,
    ).stdout
    apps = {}
    for line in out.splitlines():
        name, _, app_id = line.partition("\t")
        name, app_id = name.strip(), app_id.strip()
        if not name or not app_id or "uninstall" in name.lower():
            continue
        apps.setdefault(name[:100], app_id)
    _app_cache, _app_cache_time = apps, time.time()
    return apps


def match_choices(options, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower().strip()
    starts = [o for o in options if o.lower().startswith(current)]
    contains = [o for o in options if current in o.lower() and o not in starts]
    return [app_commands.Choice(name=o, value=o) for o in (starts + contains)[:25]]


async def app_autocomplete(interaction: discord.Interaction, current: str):
    if not is_owner(interaction):
        return []
    apps = await asyncio.to_thread(load_start_apps)
    return match_choices(sorted(apps, key=str.lower), current)


@tree.command(name="open", description="Open an app on the PC")
@app_commands.describe(app="Start typing an app name")
@app_commands.autocomplete(app=app_autocomplete)
@app_commands.check(is_owner)
async def open_cmd(interaction: discord.Interaction, app: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    apps = await asyncio.to_thread(load_start_apps)
    app_id = apps.get(app)
    if not app_id:
        lowered = {k.lower(): k for k in apps}
        key = lowered.get(app.lower())
        if key:
            app, app_id = key, apps[key]
    if not app_id:
        await interaction.followup.send(f"Couldn't find an app called **{app}**. Pick one from the list.", ephemeral=True)
        return
    await asyncio.to_thread(os.startfile, f"shell:AppsFolder\\{app_id}")
    await interaction.followup.send(f"🚀 Opened **{app}**", ephemeral=True)
    log.info("/open %s by %s", app, interaction.user.id)


def process_groups(sample_seconds: float = 1.0) -> list[dict]:
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(sample_seconds)
    cores = psutil.cpu_count() or 1
    groups: dict[str, dict] = {}
    for p in procs:
        try:
            name = p.info["name"] or ""
            if not name or name.lower() in ("system idle process",):
                continue
            cpu = p.cpu_percent(None) / cores
            mem = p.memory_info().rss
        except psutil.Error:
            continue
        g = groups.setdefault(name.lower(), {"name": name, "count": 0, "cpu": 0.0, "mem": 0})
        g["count"] += 1
        g["cpu"] += cpu
        g["mem"] += mem
    return list(groups.values())


def running_names() -> list[str]:
    totals: dict[str, int] = {}
    names: dict[str, str] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        name = p.info["name"]
        mem = p.info["memory_info"]
        if not name or name.lower() in PROTECTED_PROCESSES:
            continue
        key = name.lower()
        names.setdefault(key, name)
        totals[key] = totals.get(key, 0) + (mem.rss if mem else 0)
    return [names[k] for k in sorted(totals, key=totals.get, reverse=True)]


def find_processes(name: str) -> list[psutil.Process]:
    target = name.lower().strip()
    if not target.endswith(".exe"):
        candidates = {target, target + ".exe"}
    else:
        candidates = {target}
    me = os.getpid()
    found = []
    for p in psutil.process_iter(["name"]):
        if p.pid == me:
            continue
        if (p.info["name"] or "").lower() in candidates:
            found.append(p)
    return found


def kill_processes(procs: list[psutil.Process]) -> tuple[int, int]:
    denied = 0
    for p in procs:
        try:
            p.terminate()
        except psutil.AccessDenied:
            denied += 1
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.AccessDenied:
            denied += 1
        except psutil.NoSuchProcess:
            pass
    _, still = psutil.wait_procs(alive, timeout=2)
    return len(procs) - len(still), denied


class ConfirmKill(discord.ui.View):
    def __init__(self, name: str, procs: list[psutil.Process]):
        super().__init__(timeout=30)
        self.name = name
        self.procs = procs

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return is_owner(interaction)

    async def on_timeout(self):
        self.stop()

    @discord.ui.button(label="Kill it", style=discord.ButtonStyle.danger, emoji="💀")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content=f"Closing **{self.name}**...", view=None)
        closed, denied = await asyncio.to_thread(kill_processes, self.procs)
        msg = f"💀 Closed **{self.name}** ({closed}/{len(self.procs)} processes)"
        if denied:
            msg += f"\n⚠️ {denied} need admin rights to close."
        await interaction.edit_original_response(content=msg)
        log.info("/kill %s closed=%s denied=%s by %s", self.name, closed, denied, interaction.user.id)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def process_autocomplete(interaction: discord.Interaction, current: str):
    if not is_owner(interaction):
        return []
    names = await asyncio.to_thread(running_names)
    current = current.lower().strip()
    starts = [n for n in names if n.lower().startswith(current)]
    contains = [n for n in names if current in n.lower() and n not in starts]
    return [app_commands.Choice(name=n, value=n) for n in (starts + contains)[:25]]


@tree.command(name="kill", description="Close a running app on the PC")
@app_commands.describe(app="Start typing a running app's name, e.g. chrome")
@app_commands.autocomplete(app=process_autocomplete)
@app_commands.check(is_owner)
async def kill_cmd(interaction: discord.Interaction, app: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    clean = app.strip()
    check_name = clean.lower() if clean.lower().endswith(".exe") else clean.lower() + ".exe"
    if clean.lower() in PROTECTED_PROCESSES or check_name in PROTECTED_PROCESSES:
        await interaction.followup.send(f"🛑 **{clean}** is a Windows system process. Closing it could crash the PC.", ephemeral=True)
        return
    procs = await asyncio.to_thread(find_processes, clean)
    if not procs:
        await interaction.followup.send(f"Nothing called **{clean}** is running.", ephemeral=True)
        return
    name = procs[0].info.get("name") or clean
    mem = 0
    for p in procs:
        try:
            mem += p.memory_info().rss
        except psutil.Error:
            pass
    await interaction.followup.send(
        f"Close **{name}**? That's {len(procs)} process{'es' if len(procs) != 1 else ''} using {fmt_bytes(mem)}.\n"
        "Unsaved work in it will be lost.",
        view=ConfirmKill(name, procs),
        ephemeral=True,
    )


@tree.command(name="processes", description="Show what's using the most CPU or RAM")
@app_commands.describe(sort_by="What to sort by", count="How many to show")
@app_commands.choices(sort_by=[
    app_commands.Choice(name="RAM", value="mem"),
    app_commands.Choice(name="CPU", value="cpu"),
])
@app_commands.check(is_owner)
async def processes_cmd(
    interaction: discord.Interaction,
    sort_by: app_commands.Choice[str] | None = None,
    count: app_commands.Range[int, 5, 25] = 15,
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    key = sort_by.value if sort_by else "mem"
    groups = await asyncio.to_thread(process_groups)
    groups.sort(key=lambda g: g[key], reverse=True)
    ram = psutil.virtual_memory()
    rows = [f"{'App':<24}{'#':>3}{'CPU':>7}{'RAM':>11}"]
    for g in groups[:count]:
        name = g["name"].removesuffix(".exe")
        if len(name) > 23:
            name = name[:22] + "…"
        rows.append(f"{name:<24}{g['count']:>3}{g['cpu']:>6.1f}%{fmt_bytes(g['mem']):>11}")
    embed = discord.Embed(
        title=f"Top {count} by {'RAM' if key == 'mem' else 'CPU'}",
        description="```\n" + "\n".join(rows) + "\n```",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"CPU {psutil.cpu_percent():.0f}% · RAM {ram.percent:.0f}% · {len(groups)} apps running")
    await interaction.followup.send(embed=embed, ephemeral=True)
    log.info("/processes sort=%s by %s", key, interaction.user.id)


user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetWindowLongW.restype = ctypes.c_long
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
DWMWA_CLOAKED = 14
SW_RESTORE = 9
SW_SHOW = 5
VK_MENU = 0x12
KEYEVENTF_KEYUP = 2


def list_windows() -> list[dict]:
    windows = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or title in ("Program Manager", "Windows Input Experience"):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            app = psutil.Process(pid.value).name().removesuffix(".exe")
        except psutil.Error:
            app = "?"
        windows.append({"hwnd": int(hwnd), "title": title, "app": app})
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return windows


def window_label(w: dict) -> str:
    label = f"{w['app']} — {w['title']}"
    return label if len(label) <= 100 else label[:99] + "…"


def bring_to_front(hwnd: int) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.2)
    return user32.GetForegroundWindow() == hwnd


async def window_autocomplete(interaction: discord.Interaction, current: str):
    if not is_owner(interaction):
        return []
    windows = await asyncio.to_thread(list_windows)
    current = current.lower().strip()
    hits = [w for w in windows if current in window_label(w).lower()]
    return [app_commands.Choice(name=window_label(w), value=str(w["hwnd"])) for w in hits[:25]]


@tree.command(name="focus", description="Bring an open window to the front")
@app_commands.describe(window="Start typing an app or window title", screenshot="Send a screenshot after")
@app_commands.autocomplete(window=window_autocomplete)
@app_commands.check(is_owner)
async def focus_cmd(interaction: discord.Interaction, window: str, screenshot: bool = False):
    await interaction.response.defer(ephemeral=True, thinking=True)
    windows = await asyncio.to_thread(list_windows)
    target = next((w for w in windows if str(w["hwnd"]) == window), None)
    if not target:
        needle = window.lower().strip()
        target = next((w for w in windows if needle in window_label(w).lower()), None)
    if not target:
        await interaction.followup.send(f"No open window matches **{window}**. Pick one from the list.", ephemeral=True)
        return
    ok = await asyncio.to_thread(bring_to_front, target["hwnd"])
    msg = f"🪟 Brought **{window_label(target)}** to the front" if ok else (
        f"⚠️ Windows blocked bringing **{window_label(target)}** to the front. It may be flashing in the taskbar instead."
    )
    file = None
    if screenshot:
        await asyncio.sleep(0.4)
        data, filename, _ = await asyncio.to_thread(grab_screenshot, 1)
        file = discord.File(io.BytesIO(data), filename=filename)
    if file:
        await interaction.followup.send(msg, file=file, ephemeral=True)
    else:
        await interaction.followup.send(msg, ephemeral=True)
    log.info("/focus %s ok=%s by %s", target["title"], ok, interaction.user.id)


for cmd in (status_cmd, screenshot_cmd, open_cmd, kill_cmd, processes_cmd, focus_cmd):
    cmd.allowed_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)
    cmd.allowed_installs = app_commands.AppInstallationType(guild=True, user=True)


@client.event
async def on_ready():
    log.info("Logged in as %s", client.user)
    if GUILD_ID and not client.get_guild(GUILD_ID):
        log.warning(
            "Bot isn't in server %s yet. Invite it: "
            "https://discord.com/oauth2/authorize?client_id=%s&scope=bot+applications.commands",
            GUILD_ID,
            client.application_id,
        )
    synced = await tree.sync()
    log.info("Synced %d commands", len(synced))


def main():
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
    if not OWNER_ID:
        raise SystemExit("OWNER_ID is missing. Put your Discord user ID in .env.")
    if KEEP_AWAKE:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        if KEEP_SCREEN_ON:
            flags |= ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        log.info("Keeping the PC awake%s while the bot runs", " with the screen on" if KEEP_SCREEN_ON else "")
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
