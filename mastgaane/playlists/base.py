from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Track:
    id: str
    title: str
    artist: str
    url: str


class PlaylistAdapter(Protocol):
    name: str

    def available(self) -> bool: ...
    def search(self, title: str, artist: str) -> Track | None: ...
    def ensure_playlist(self, name: str) -> str: ...
    def add(self, playlist_id: str, track: Track) -> None: ...
    def playlist_url(self, playlist_id: str) -> str: ...


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def score_match(q_title: str, q_artist: str, r_title: str, r_artist: str) -> float:
    """Cheap relevance score to pick the best search hit."""
    qt, qa, rt, ra = _norm(q_title), _norm(q_artist), _norm(r_title), _norm(r_artist)
    score = 0.0
    if qt and (qt in rt or rt in qt):
        score += 0.6
    if qa and ra and (qa in ra or ra in qa):
        score += 0.4
    elif qa and qa.split()[0] in ra:
        score += 0.2
    return score
