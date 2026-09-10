"""Shared utilities: rate-limit retry, failure log, JSON report writer.

The helpers in this module use Spotify's February 2026 Web API endpoints:
library writes go through /me/library and playlist follow/unfollow uses
spotify:playlist URIs with the same generic library endpoint.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from spotipy.exceptions import SpotifyException

LOGS_DIR = Path.cwd() / "logs"
LIBRARY_BATCH_MAX = 40


def safe_call(fn: Callable, *args: Any, max_retries: int = 3, **kwargs: Any):
    """Invoke a Spotify call with retry on 429 and transient 5xx errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as exc:
            last_exc = exc
            status = exc.http_status or 0
            if status == 429:
                retry_after = 1
                headers = getattr(exc, "headers", None) or {}
                try:
                    retry_after = int(headers.get("Retry-After", 1))
                except (TypeError, ValueError):
                    retry_after = 1
                time.sleep(retry_after + 1)
                continue
            if 500 <= status < 600:
                time.sleep(2**attempt)
                continue
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(2**attempt)
            continue
    if last_exc:
        raise last_exc


@dataclass
class FailureLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, kind: str, **payload: Any) -> None:
        self.entries.append({"type": kind, **payload})

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class MigrationReport:
    started_at: str
    source_user_id: str
    source_user_name: str
    destination_user_id: str
    destination_user_name: str
    mode: str
    source_counts: dict
    destination_counts: dict
    cleanup: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)
    finished_at: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, ensure_ascii=False)


def save_report(report: MigrationReport) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"migration_{ts}.json"
    path.write_text(report.to_json(), encoding="utf-8")
    return path


def chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _track_uri(uri_or_id: str) -> str:
    """Return a canonical spotify:track URI from either a URI or raw ID."""
    if uri_or_id.startswith("spotify:track:"):
        return uri_or_id
    return f"spotify:track:{uri_or_id.split(':')[-1]}"


def _album_uri(uri_or_id: str) -> str:
    """Return a canonical spotify:album URI from either a URI or raw ID."""
    if uri_or_id.startswith("spotify:album:"):
        return uri_or_id
    return f"spotify:album:{uri_or_id.split(':')[-1]}"


def _artist_uri(uri_or_id: str) -> str:
    """Return a canonical spotify:artist URI from either a URI or raw ID."""
    if uri_or_id.startswith("spotify:artist:"):
        return uri_or_id
    return f"spotify:artist:{uri_or_id.split(':')[-1]}"


def _playlist_uri(uri_or_id: str) -> str:
    """Return a canonical spotify:playlist URI from either a URI or raw ID."""
    if uri_or_id.startswith("spotify:playlist:"):
        return uri_or_id
    return f"spotify:playlist:{uri_or_id.split(':')[-1]}"


def _put_library(sp, uris: list[str]):
    if not uris:
        return None
    if len(uris) > LIBRARY_BATCH_MAX:
        raise ValueError(f"Spotify /me/library accepts at most {LIBRARY_BATCH_MAX} URIs")
    return sp._put("me/library", uris=",".join(uris))


def _delete_library(sp, uris: list[str]):
    if not uris:
        return None
    if len(uris) > LIBRARY_BATCH_MAX:
        raise ValueError(f"Spotify /me/library accepts at most {LIBRARY_BATCH_MAX} URIs")
    return sp._delete("me/library", uris=",".join(uris))


def saved_tracks_add(sp, uris: list[str]):
    return _put_library(sp, [_track_uri(value) for value in uris])


def saved_tracks_delete(sp, uris: list[str]):
    return _delete_library(sp, [_track_uri(value) for value in uris])


def saved_albums_add(sp, album_ids: list[str]):
    return _put_library(sp, [_album_uri(value) for value in album_ids])


def saved_albums_delete(sp, album_ids: list[str]):
    return _delete_library(sp, [_album_uri(value) for value in album_ids])


def follow_artists(sp, artist_ids: list[str]):
    return _put_library(sp, [_artist_uri(value) for value in artist_ids])


def unfollow_artists(sp, artist_ids: list[str]):
    return _delete_library(sp, [_artist_uri(value) for value in artist_ids])


def follow_playlist(sp, playlist_id: str):
    return _put_library(sp, [_playlist_uri(playlist_id)])


def unfollow_playlist(sp, playlist_id: str):
    return _delete_library(sp, [_playlist_uri(playlist_id)])


def playlist_add_items(sp, playlist_id: str, uris: list[str]):
    """Append playlist items in the exact order supplied."""
    if not uris:
        return None
    if len(uris) > 100:
        raise ValueError("Spotify playlist item endpoint accepts at most 100 URIs")
    pid = playlist_id.split(":")[-1]
    return sp._post(
        f"playlists/{pid}/items",
        payload={"uris": uris},
    )


def create_playlist(
    sp,
    name: str,
    *,
    public: bool = True,
    collaborative: bool = False,
    description: str = "",
):
    """Create a playlist with the current 2026 /me/playlists endpoint."""
    payload = {
        "name": name,
        "public": public,
        "collaborative": collaborative,
        "description": description,
    }
    return sp._post("me/playlists", payload=payload)
