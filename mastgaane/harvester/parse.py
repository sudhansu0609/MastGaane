"""Defensive extraction of Reel items from any Instagram JSON payload.

Instagram rotates GraphQL doc_ids and nests media under different keys depending on the
surface, so we do not trust paths. We walk every dict in the payload and treat anything that
looks like a clip (has a shortcode + clip/video markers) as an item, pulling each field from
whichever of the known variants is present.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass
class ReelItem:
    shortcode: str
    pk: str | None = None
    owner: str = ""
    caption: str = ""
    taken_at: datetime | None = None
    song_title: str | None = None
    artist: str | None = None
    is_original_audio: bool = False
    audio_id: str | None = None
    video_url: str | None = None
    thumb_url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def has_music(self) -> bool:
        return bool(self.song_title) and not self.is_original_audio


def _walk(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _get(d: Any, *path, default=None):
    cur: Any = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and len(cur) > p:
            cur = cur[p]
        else:
            return default
        if cur is None:
            return default
    return cur


def _looks_like_clip(d: dict) -> bool:
    code = d.get("code") or d.get("shortcode")
    if not isinstance(code, str) or not code:
        return False
    markers = ("clips_metadata", "clips_music_attribution_info", "video_versions", "video_url", "product_type")
    if not any(k in d for k in markers):
        return False
    if d.get("product_type") not in (None, "clips"):
        return False
    mt = d.get("media_type")
    if mt is not None and mt != 2:  # 2 == video
        return False
    if d.get("is_video") is False:
        return False
    return True


def _caption(d: dict) -> str:
    cap = d.get("caption")
    if isinstance(cap, dict):
        return cap.get("text") or ""
    if isinstance(cap, str):
        return cap
    return _get(d, "edge_media_to_caption", "edges", 0, "node", "text", default="") or ""


def _owner(d: dict) -> str:
    for k in ("user", "owner"):
        u = d.get(k)
        if isinstance(u, dict) and u.get("username"):
            return u["username"]
    return ""


def _music(d: dict) -> tuple[str | None, str | None, bool, str | None]:
    """Return (title, artist, is_original, audio_id) from whichever attribution shape exists."""
    cm = d.get("clips_metadata") or {}
    mai = _get(cm, "music_info", "music_asset_info") or {}
    if mai.get("title"):
        return (
            mai.get("title"),
            mai.get("display_artist") or mai.get("artist_name"),
            False,
            str(mai.get("audio_cluster_id") or mai.get("audio_id") or "") or None,
        )
    osi = cm.get("original_sound_info") or {}
    if osi:
        title = osi.get("original_audio_title") or "Original audio"
        artist = _get(osi, "ig_artist", "username") or _owner(d) or None
        return title, artist, True, str(osi.get("audio_asset_id") or "") or None
    att = d.get("clips_music_attribution_info") or {}
    if att:
        orig = bool(att.get("uses_original_audio"))
        return att.get("song_name"), att.get("artist_name"), orig, str(att.get("audio_id") or "") or None
    return None, None, False, None


def _ts(d: dict) -> datetime | None:
    t = d.get("taken_at") or d.get("taken_at_timestamp")
    if isinstance(t, (int, float)) and t > 0:
        return datetime.fromtimestamp(t, tz=timezone.utc)
    return None


def to_item(d: dict) -> ReelItem:
    title, artist, orig, audio_id = _music(d)
    return ReelItem(
        shortcode=d.get("code") or d.get("shortcode"),
        pk=str(d.get("pk") or d.get("id") or "") or None,
        owner=_owner(d),
        caption=_caption(d),
        taken_at=_ts(d),
        song_title=title,
        artist=artist,
        is_original_audio=orig,
        audio_id=audio_id,
        video_url=_get(d, "video_versions", 0, "url") or d.get("video_url"),
        thumb_url=_get(d, "image_versions2", "candidates", 0, "url") or d.get("display_url") or d.get("thumbnail_src"),
        raw=d,
    )


def extract_items(payload: Any) -> list[ReelItem]:
    seen: dict[str, ReelItem] = {}
    for d in _walk(payload):
        if _looks_like_clip(d):
            item = to_item(d)
            prev = seen.get(item.shortcode)
            # prefer the richer variant if the same clip appears twice
            if prev is None or (item.song_title and not prev.song_title):
                seen[item.shortcode] = item
    return list(seen.values())
