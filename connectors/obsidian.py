"""Keep the Obsidian desktop app running.

BatPuter writes memory into an Obsidian vault and uses the official `obsidian`
CLI for link-preserving renames. Both Obsidian Sync and the CLI need the desktop
app running, so we launch it at startup if it isn't already. macOS only;
best-effort — never raises.
"""
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

_CLI = "obsidian"


def ensure_running(vault_name: str) -> None:
    if is_running():
        logger.info("Obsidian already running")
        return
    # The official Obsidian CLI launches the app on its first command.
    if shutil.which(_CLI):
        try:
            subprocess.run(
                [_CLI, "vault", f"vault={vault_name}", "info=name"],
                capture_output=True, text=True, timeout=30,
            )
            logger.info("Launched Obsidian via the official CLI for continuous sync")
            return
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("obsidian CLI launch failed (%s); falling back to `open`", e)
    try:
        subprocess.run(["open", "-ga", "Obsidian"], capture_output=True, timeout=15)
        logger.info("Launched Obsidian via `open` for continuous sync")
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Could not launch Obsidian: %s", e)


def is_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0
    except OSError:
        return False
