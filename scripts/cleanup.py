import shutil
import re
import logging
from pathlib import Path
from typing import Set


def identify_year(filename: str) -> str:
    """
    Extract year from filename.
    Matches:
      - *_2024.json -> 2024
      - *_24.json -> 2024
    """
    # Check for 4 digit year at end of filename (before .json)
    m4 = re.search(r"_(\d{4})\.json$", filename)
    if m4:
        return m4.group(1)

    # Check for 2 digit year at end of filename
    m2 = re.search(r"_(\d{2})\.json$", filename)
    if m2:
        return f"20{m2.group(1)}"

    # Check for 2 digit year in middle (like HYDROARC_123_24_...)
    # But usually it is at the end.

    return "unknown"


def move_to_history(filename: str, current_dir: Path, history_dir_base: Path):
    year = identify_year(filename)
    # Target structure: history/<year>/navwarns/
    # Or history/<year>/prips/ if it came from prips?
    # The user said "old (obsolete) messages are in the history dir".

    # If the file is a prip (starts with PRIP), maybe put in history/<year>/prips?
    # If standard navwarn, history/<year>/navwarns?

    if filename.startswith("PRIP"):
        dest_subdir = "prips"
    else:
        dest_subdir = "navwarns"

    dest_dir = history_dir_base / year / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    src = current_dir / filename
    dst = dest_dir / filename

    logging.info(f"Moving obsolete message {filename} to {dest_dir}")
    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        logging.error(f"Failed to move {src} to {dst}: {e}")


def cleanup(
    active_files: Set[str],
    current_dir: Path,
    file_pattern: str,
    history_dir_base: Path = Path("history"),
):
    """
    Moves files matching file_pattern in current_dir that are NOT in active_files to history.
    """
    if not current_dir.exists():
        return

    logging.info(f"Cleaning up {current_dir} matching {file_pattern}...")

    # glob returns full paths
    for file_path in current_dir.glob(file_pattern):
        filename = file_path.name
        if filename not in active_files:
            move_to_history(filename, current_dir, history_dir_base)
