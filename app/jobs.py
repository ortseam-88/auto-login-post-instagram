# create by Ortseam
"""Manifest v1 ketat; path media relatif folder manifest, bukan direktori kerja.

Caption dihitung dengan len(str): Unicode code point, bukan byte UTF-8 atau
jumlah glyph. Composer tetap wajib memeriksa counter/draft sebelum Share.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path

from accounts import Account
from models import Job, MediaItem, PersonTag, UnsupportedOption

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".avif", ".heic", ".heif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov"})
PHOTO_CROPS = frozenset({"Original", "1:1", "4:5", "16:9"})
REEL_CROPS = frozenset({"Original", "1:1", "9:16", "16:9"})
FILTERS = frozenset({
    "Aden", "Clarendon", "Crema", "Gingham", "Juno", "Lark", "Ludwig",
    "Moon", "Original", "Perpetua", "Reyes", "Slumber",
})
ADJUSTMENTS = {
    "brightness": (-100, 100), "contrast": (-100, 100), "fade": (-100, 100),
    "saturation": (-100, 100), "temperature": (-100, 100), "vignette": (0, 100),
}
JOB_FIELDS = frozenset(Job.__dataclass_fields__)
CONTENT_FIELDS = JOB_FIELDS - {"job_id", "account"}
MEDIA_FIELDS = frozenset(MediaItem.__dataclass_fields__)
TAG_FIELDS = frozenset(PersonTag.__dataclass_fields__)
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}\Z")


def _error(job_id: str, field: str, message: str) -> ValueError:
    return ValueError(f"Job {job_id} / {field}: {message}")


def _object(value, allowed, job_id, field):
    if not isinstance(value, dict):
        raise _error(job_id, field, "harus objek JSON.")
    if set(value) - allowed:
        # Jangan tampilkan nilai/key asing: dapat berisi kredensial terselip.
        raise UnsupportedOption(f"Job {job_id} / {field}: ada field tidak dikenal/tidak didukung.")
    return value


def _string(value, job_id, field, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise _error(job_id, field, "harus teks" + (" tidak kosong." if nonempty else "."))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _error(job_id, field, "Unicode tidak valid.") from None
    if "\0" in value:
        raise _error(job_id, field, "karakter NUL tidak diizinkan.")
    return value


def _optional_bool(value, job_id, field):
    if value is not None and type(value) is not bool:
        raise _error(job_id, field, "harus true, false, atau null; bukan angka/teks.")
    return value


def _number(value, job_id, field):
    if type(value) not in (int, float):
        raise _error(job_id, field, "harus angka finite nonnegatif.")
    try:
        number = float(value)
    except OverflowError:
        raise _error(job_id, field, "harus angka finite nonnegatif.") from None
    if not math.isfinite(number) or number < 0:
        raise _error(job_id, field, "harus angka finite nonnegatif.")
    return number


def _integer(value, minimum, maximum, job_id, field):
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(job_id, field, f"harus bilangan bulat {minimum}..{maximum}.")
    return value


def _file(value, directory, extensions, job_id, field, *, check_files=True):
    value = _string(value, job_id, field, nonempty=True)
    try:
        path = Path(value)
        path = (path if path.is_absolute() else directory / path).resolve(strict=check_files)
        if check_files and not path.is_file():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise _error(job_id, field, "file lokal tidak ditemukan/tidak dapat dibaca.") from None
    if path.suffix.lower() not in extensions:
        raise _error(job_id, field, "ekstensi media tidak didukung.")
    return path


def _username(value, job_id, field):
    value = _string(value, job_id, field)
    if not USERNAME.fullmatch(value):
        raise _error(job_id, field, "gunakan username Instagram tanpa @/spasi.")
    return value.casefold()


def _tags(value, job_id, field, *, coordinates):
    if not isinstance(value, list):
        raise _error(job_id, field, "harus array objek username dan x/y opsional.")
    tags = []
    seen = set()
    for index, raw in enumerate(value):
        at = f"{field}[{index}]"
        raw = _object(raw, TAG_FIELDS, job_id, at)
        username = _username(raw.get("username"), job_id, f"{at}.username")
        if username in seen:
            raise _error(job_id, at, "username tag duplikat.")
        seen.add(username)
        x, y = raw.get("x"), raw.get("y")
        if (x is None) != (y is None):
            raise _error(job_id, at, "x dan y harus keduanya diisi atau keduanya kosong.")
        if x is not None:
            if not coordinates:
                raise _error(job_id, at, "koordinat hanya untuk tag foto, bukan Reel.")
            x, y = _number(x, job_id, f"{at}.x"), _number(y, job_id, f"{at}.y")
            if x > 1 or y > 1:
                raise _error(job_id, at, "koordinat x/y harus 0..1.")
        tags.append(PersonTag(username, x, y))
    return tuple(tags)


def _media(raw, directory, job_id, index, kind, *, check_files=True):
    at = f"media[{index}]"
    raw = _object(raw, MEDIA_FIELDS, job_id, at)
    path = _file(raw.get("path"), directory, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS, job_id, f"{at}.path", check_files=check_files)
    photo = path.suffix.lower() in IMAGE_EXTENSIONS
    alt_text = _string(raw.get("alt_text", ""), job_id, f"{at}.alt_text")
    tags = _tags(raw.get("people_tags", []), job_id, f"{at}.people_tags", coordinates=True)
    crop = raw.get("crop")
    if crop is not None:
        crop = _string(crop, job_id, f"{at}.crop")
        choices = REEL_CROPS if kind == "reel" else PHOTO_CROPS
        if crop not in choices:
            raise _error(job_id, f"{at}.crop", "rasio tidak didukung untuk jenis job ini.")
    zoom = raw.get("zoom")
    if zoom is not None:
        zoom = _integer(zoom, 0, 100, job_id, f"{at}.zoom")
    filter_name = raw.get("filter_name")
    if filter_name is not None:
        filter_name = _string(filter_name, job_id, f"{at}.filter_name")
        if filter_name not in FILTERS:
            raise _error(job_id, f"{at}.filter_name", "filter tidak dikenal pada composer web.")
    adjustments = _object(raw.get("adjustments", {}), ADJUSTMENTS.keys(), job_id, f"{at}.adjustments")
    adjustments = {
        name: _integer(value, *ADJUSTMENTS[name], job_id, f"{at}.adjustments.{name}")
        for name, value in adjustments.items()
    }
    if not photo:
        for name, requested in (("alt_text", bool(alt_text)), ("people_tags", bool(tags)),
                                ("zoom", zoom is not None), ("filter_name", filter_name is not None),
                                ("adjustments", bool(adjustments))):
            if requested:
                raise UnsupportedOption(f"Job {job_id} / {at}.{name}: hanya didukung untuk foto.")
    return MediaItem(path, alt_text, tags, crop, zoom, filter_name, adjustments)


def _job(raw, directory, accounts, passwords, index):
    job_id = f"#{index + 1}"
    raw = _object(raw, JOB_FIELDS, job_id, "job")
    value = raw.get("job_id")
    if not isinstance(value, str) or not JOB_ID.fullmatch(value):
        raise _error(job_id, "job_id", "gunakan ID stabil 1..128 karakter ASCII huruf/angka/._:-.")
    if any(password in value for password in passwords):
        raise ValueError("Job ID tidak boleh memuat kredensial akun.")
    job_id = value
    account = _string(raw.get("account"), job_id, "account", nonempty=True).strip().casefold()
    if account not in accounts:
        raise _error(job_id, "account", "akun tidak ditemukan pada input akun.")
    payload = {key: value for key, value in raw.items() if key in CONTENT_FIELDS}
    return Job(job_id=job_id, account=accounts[account], **parse_content(payload, directory, job_id))


def parse_content(raw, directory: Path, label: str, *, check_files: bool = True) -> dict:
    """Validasi konten tanpa akun; engine dan katalog memakai aturan yang sama."""
    job_id = label
    raw = _object(raw, CONTENT_FIELDS, job_id, "konten")
    kind = _string(raw.get("kind"), job_id, "kind")
    if kind not in {"photo", "reel", "carousel"}:
        raise _error(job_id, "kind", "hanya photo, reel, atau carousel; teks saja tidak didukung.")
    raw_media = raw.get("media")
    if not isinstance(raw_media, list) or not raw_media:
        raise _error(job_id, "media", "wajib array media berurutan, bukan job teks saja.")
    if kind == "carousel":
        if not 2 <= len(raw_media) <= 20:
            raise _error(job_id, "media", "carousel memerlukan 2..20 item berurutan eksplisit.")
    elif len(raw_media) != 1:
        raise _error(job_id, "media", "photo/reel memerlukan tepat satu media.")
    media = tuple(_media(item, directory, job_id, i, kind, check_files=check_files) for i, item in enumerate(raw_media))
    if kind == "photo" and media[0].path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise _error(job_id, "media[0].path", "photo memerlukan satu gambar.")
    if kind == "reel" and media[0].path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise _error(job_id, "media[0].path", "reel memerlukan satu video.")
    if kind == "carousel" and len({item.crop for item in media}) != 1:
        raise _error(job_id, "media.crop", "semua item carousel harus memakai crop sama atau semua tanpa crop.")
    caption = _string(raw.get("caption", ""), job_id, "caption")
    if len(caption) > 2200:
        raise _error(job_id, "caption", "melebihi 2200 Unicode code point.")
    if len(re.findall(r"#[\w]+", caption, flags=re.UNICODE)) > 5:
        raise _error(job_id, "caption", "lebih dari 5 hashtag; kurangi hashtag sebelum menjalankan job.")
    location = raw.get("location")
    if location is not None:
        location = _string(location, job_id, "location", nonempty=True)
    collaborators = raw.get("collaborators", [])
    if not isinstance(collaborators, list):
        raise _error(job_id, "collaborators", "harus array username.")
    collaborators = tuple(_username(v, job_id, f"collaborators[{i}]") for i, v in enumerate(collaborators))
    if len(set(collaborators)) != len(collaborators):
        raise _error(job_id, "collaborators", "username kolaborator duplikat.")
    tags = _tags(raw.get("people_tags", []), job_id, "people_tags", coordinates=False)
    if tags and kind != "reel":
        raise _error(job_id, "people_tags", "tag photo/carousel harus di media[].people_tags.")
    bools = {name: _optional_bool(raw.get(name), job_id, name) for name in (
        "ai_label", "comments_enabled", "hide_like_view_counts", "keep_original_audio"
    )}
    times = {name: (_number(raw[name], job_id, name) if raw.get(name) is not None else None)
             for name in ("cover_time", "trim_start", "trim_end")}
    cover_path = raw.get("cover_path")
    if cover_path is not None:
        cover_path = _file(cover_path, directory, {".jpg", ".jpeg", ".png"}, job_id, "cover_path", check_files=check_files)
    if kind != "reel" and (cover_path is not None or any(v is not None for v in times.values())
                           or bools["keep_original_audio"] is not None):
        raise _error(job_id, "cover/trim/keep_original_audio", "opsi ini hanya untuk reel.")
    if cover_path is not None and times["cover_time"] is not None:
        raise _error(job_id, "cover_path/cover_time", "pilih salah satu sumber cover.")
    start, end = times["trim_start"], times["trim_end"]
    if (start is None) != (end is None):
        raise _error(job_id, "trim_start/trim_end", "kedua batas trim harus diisi bersama.")
    if start is not None and start >= end:
        raise _error(job_id, "trim_start/trim_end", "trim_start harus lebih kecil dari trim_end.")
    return dict(kind=kind, media=media, caption=caption, location=location,
                collaborators=collaborators, people_tags=tags,
                cover_path=cover_path, **bools, **times)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Manifest: key JSON duplikat tidak diizinkan.")
        value[key] = item
    return value


def load_jobs(path: Path, accounts: list[Account]) -> list[Job]:
    """Baca seluruh manifest sebelum membuka browser; error tidak memuat nilai rahasia."""
    try:
        path = Path(path).resolve(strict=True)
        document = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Manifest job tidak dapat dibaca atau bukan JSON UTF-8 valid.") from None
    document = _object(document, {"version", "jobs"}, "manifest", "root")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("Manifest / version: hanya integer 1 yang didukung.")
    entries = document.get("jobs")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest / jobs: wajib array berisi minimal satu job bermedia.")
    identities = {account.identifier.casefold(): account.identifier for account in accounts}
    if len(identities) != len(accounts):
        raise ValueError("Input akun memiliki identifier duplikat case-insensitive.")
    passwords = {account.password for account in accounts if account.password}
    jobs = []
    seen = set()
    for index, entry in enumerate(entries):
        job = _job(entry, path.parent, identities, passwords, index)
        if job.job_id in seen:
            raise _error(job.job_id, "job_id", "ID duplikat dalam manifest.")
        seen.add(job.job_id)
        jobs.append(job)
    return jobs


def _content_hash(path: Path, job_id: str, field: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError:
        raise _error(job_id, field, "file tidak dapat dibaca untuk SHA256; job dihentikan.") from None
    return digest.hexdigest()


def digest_job(job: Job) -> str:
    """SHA256 bytes media/cover + semua opsi normalisasi; lokasi file bukan identitas.

    Panggil lagi tepat sebelum Share untuk mendeteksi media berubah sejak prepare.
    """
    payload = asdict(job)
    payload["account"] = job.account.casefold()
    for index, (item, source) in enumerate(zip(payload["media"], job.media)):
        item["path"] = _content_hash(source.path, job.job_id, f"media[{index}].path")
    if job.cover_path is not None:
        payload["cover_path"] = _content_hash(job.cover_path, job.job_id, "cover_path")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
