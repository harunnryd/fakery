import json
import time
from pathlib import Path

COOKIE_ALLOW_PATTERNS = (
    "https://[*.]meet.google.com",
    "https://[*.]google.com",
    "https://[*.]googleusercontent.com",
    "https://[*.]gstatic.com",
)


def init_profile_defaults(profile_dir: Path) -> None:
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    _seed_bookmarks(default_dir)
    _patch_preferences(default_dir)


WEBKIT_TIMESTAMP_SCALE = 1_000_000


def _seed_bookmarks(default_dir: Path) -> None:
    bookmarks_path = default_dir / "Bookmarks"
    if bookmarks_path.exists():
        return
    ts = str(int(time.time() * WEBKIT_TIMESTAMP_SCALE))
    next_id = 1

    def bookmark(name: str, url: str) -> dict:
        nonlocal next_id
        next_id += 1
        return {"type": "url", "id": str(next_id), "name": name, "url": url, "date_added": ts}

    def folder(name: str, children: list[dict]) -> dict:
        nonlocal next_id
        next_id += 1
        return {
            "type": "folder",
            "id": str(next_id),
            "name": name,
            "date_added": ts,
            "date_modified": ts,
            "children": children,
        }

    bookmarks = {
        "checksum": "",
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "id": "1",
                "name": "Bookmarks bar",
                "date_added": ts,
                "date_modified": ts,
                "children": [
                    folder(
                        "Meetings",
                        [
                            bookmark("Google Meet", "https://meet.google.com/"),
                            bookmark("Google Calendar", "https://calendar.google.com/"),
                        ],
                    ),
                    folder(
                        "Work",
                        [
                            bookmark("GitHub", "https://github.com/"),
                            bookmark("Gmail", "https://mail.google.com/"),
                        ],
                    ),
                ],
            },
            "other": {"type": "folder", "id": "2", "name": "Other bookmarks", "children": []},
            "synced": {"type": "folder", "id": "3", "name": "Mobile bookmarks", "children": []},
        },
        "version": 1,
    }
    bookmarks_path.write_text(json.dumps(bookmarks, indent=2))


def _cookie_exceptions() -> dict:
    return {f"{pattern},*": {"setting": 1} for pattern in COOKIE_ALLOW_PATTERNS}


def _patch_preferences(default_dir: Path) -> None:
    prefs_path = default_dir / "Preferences"
    prefs: dict = {}
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text())
        except Exception:
            prefs = {}

    profile_node = prefs.setdefault("profile", {})
    profile_node["exit_type"] = "Normal"
    profile_node["exited_cleanly"] = True
    cookies_node = (
        profile_node.setdefault("content_settings", {})
        .setdefault("exceptions", {})
        .setdefault("cookies", {})
    )
    cookies_node.update(_cookie_exceptions())

    prefs_path.write_text(json.dumps(prefs, indent=2))
