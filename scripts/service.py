"""Install or stop this project's per-user macOS LaunchAgent."""
import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "local.koi.codex-monitor"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    if args.action == "stop":
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=False)
        if PLIST.exists():
            PLIST.unlink()
        print("Codex Monitor 已停止，登录启动已移除。")
        return
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    config = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", "monitor.server"],
              "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
              "ThrottleInterval": 10, "StandardOutPath": str(runtime / "server.log"),
              "StandardErrorPath": str(runtime / "error.log"),
              "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}}
    old = None
    if PLIST.exists():
        with PLIST.open("rb") as stream:
            old = plistlib.load(stream)
        if old.get("WorkingDirectory") != str(ROOT):
            raise SystemExit("同名服务属于其他目录，请先检查 LaunchAgent。")
    loaded = subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], capture_output=True).returncode == 0
    if loaded and old == config:
        subprocess.run(["launchctl", "kickstart", "-k", f"{DOMAIN}/{LABEL}"], check=True)
        print("Codex Monitor 已重新启动。端口：8790")
        return
    if loaded:
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], capture_output=True)
    with PLIST.open("wb") as stream:
        plistlib.dump(config, stream)
    for attempt in range(10):
        result = subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], capture_output=True)
        if result.returncode == 0:
            break
        if attempt == 9:
            raise SystemExit("启动失败：" + result.stderr.decode(errors="replace"))
        time.sleep(0.3)
    print("Codex Monitor 已启动，登录后自动运行。端口：8790")


if __name__ == "__main__":
    main()
