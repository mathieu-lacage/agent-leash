import shutil
import subprocess


def send_notification(summary: str, body: str = "") -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "--app-name=aleash", summary, body],
            timeout=2.0,
            capture_output=True,
        )
    except Exception:
        pass
