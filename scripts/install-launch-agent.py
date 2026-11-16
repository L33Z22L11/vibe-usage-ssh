"""Install the macOS login service for this checkout."""

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "dev.vibe-usage.ssh"


def main():
    logs = Path.home() / "Library" / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(ROOT / "scripts" / "run.sh")],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(logs / "vibe-usage-ssh.log"),
        "StandardErrorPath": str(logs / "vibe-usage-ssh.log"),
    }
    target.write_bytes(plistlib.dumps(config))
    print(f"已安装 {target}")
    print(f"启动：launchctl bootstrap gui/$(id -u) {target}")


if __name__ == "__main__":
    main()
