# create by Ortseam
"""Kontrak tunggal job publikasi; kredensial berada di accounts, bukan job."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PersonTag:
    username: str
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class MediaItem:
    path: Path
    alt_text: str = ""
    people_tags: tuple[PersonTag, ...] = ()
    crop: str | None = None
    zoom: int | None = None
    filter_name: str | None = None
    adjustments: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    job_id: str
    account: str
    kind: str
    media: tuple[MediaItem, ...]
    caption: str = ""
    location: str | None = None
    collaborators: tuple[str, ...] = ()
    ai_label: bool | None = None
    comments_enabled: bool | None = None
    hide_like_view_counts: bool | None = None
    people_tags: tuple[PersonTag, ...] = ()
    cover_path: Path | None = None
    cover_time: float | None = None
    trim_start: float | None = None
    trim_end: float | None = None
    keep_original_audio: bool | None = None


@dataclass(frozen=True)
class PublishResult:
    status: str
    detail: str
    permalink: str | None = None


class UnsupportedOption(ValueError):
    """Opsi yang diminta tidak tersedia/terverifikasi pada UI saat ini."""


class DraftValidationError(ValueError):
    """Isi draft berbeda dari job atau gagal disiapkan sebelum Share."""
