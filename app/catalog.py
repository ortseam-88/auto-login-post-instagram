# create by Ortseam
"""Katalog konten tanpa akun, migrasi identitas lama, dan rencana run eksplisit."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from accounts import Account
from jobs import CONTENT_FIELDS, JOB_FIELDS, JOB_ID, _unique_object, parse_content
from utils import read_state_text

CONTENT_ID = re.compile(r"[0-9a-f]{32}\Z")


def _account_key(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Identifier akun harus teks.")
    value = value.strip()
    if not value or "|" in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Identifier akun tidak valid.")
    return value.casefold()


def _payload(content: dict) -> dict:
    return {key: value for key, value in content.items() if key not in {"content_id", "name"}}


def validate_manifest(document: dict, directory: Path, *, check_files: bool = True) -> list[str]:
    """Validasi dokumen API v2 tanpa bergantung pada inventaris akun."""
    if not isinstance(document, dict) or set(document) != {"version", "contents"}:
        raise ValueError("Katalog harus memakai {version: 2, contents}; identitas posting dikelola program.")
    if type(document["version"]) is not int or document["version"] != 2 or not isinstance(document["contents"], list):
        raise ValueError("Katalog harus memakai versi 2 dengan daftar contents.")
    content_ids, names = [], set()
    seen = set()
    for content in document["contents"]:
        if not isinstance(content, dict):
            raise ValueError("Setiap konten harus berupa objek JSON.")
        content_id, name = content.get("content_id"), content.get("name")
        if not isinstance(content_id, str) or not CONTENT_ID.fullmatch(content_id):
            raise ValueError("Identitas konten harus 32 karakter heksadesimal kecil.")
        if content_id in seen:
            raise ValueError("Identitas konten duplikat; buat salinan dengan identitas baru.")
        if not isinstance(name, str) or not JOB_ID.fullmatch(name):
            raise ValueError("Nama konten harus 1..128 karakter huruf/angka/._:-.")
        if name in names:
            raise ValueError("Nama konten duplikat; gunakan nama berbeda.")
        parse_content(_payload(content), directory, name, check_files=check_files)
        seen.add(content_id)
        names.add(name)
        content_ids.append(content_id)
    return content_ids


def _validate_bindings(bindings) -> None:
    if not isinstance(bindings, dict):
        raise ValueError("Riwayat identitas konten rusak; jangan hapus riwayat posting.")
    seen = set()
    for content_id, pairs in bindings.items():
        if not isinstance(content_id, str) or not CONTENT_ID.fullmatch(content_id) or not isinstance(pairs, dict):
            raise ValueError("Riwayat identitas konten tidak valid.")
        for account, attempts in pairs.items():
            if _account_key(account) != account or not isinstance(attempts, list) or not attempts:
                raise ValueError("Riwayat identitas akun tidak valid.")
            for job_id in attempts:
                if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or job_id in seen:
                    raise ValueError("Identitas posting rusak atau dipakai oleh lebih dari satu konten.")
                seen.add(job_id)


def _normalized_payload(payload: dict, directory: Path, label: str) -> dict:
    parsed = parse_content(payload, directory, label, check_files=False)
    normalized = {**parsed, "media": [asdict(item) for item in parsed["media"]],
                  "people_tags": [asdict(tag) for tag in parsed["people_tags"]],
                  "collaborators": list(parsed["collaborators"])}
    # Pengelompokan mengikuti ejaan/urutan path editor, bukan lokasi program.
    for item, original in zip(normalized["media"], payload["media"]):
        item["path"] = original["path"]
    normalized["cover_path"] = payload.get("cover_path")
    return normalized


def _migrate_legacy(document: dict, directory: Path) -> dict:
    if set(document) != {"version", "jobs"} or not isinstance(document.get("jobs"), list):
        raise ValueError("Manifest lama tidak memakai struktur {version: 1, jobs}.")
    contents, bindings, groups, seen_ids = [], {}, {}, set()
    for entry in document["jobs"]:
        if not isinstance(entry, dict) or set(entry) - JOB_FIELDS:
            raise ValueError("Konten lama memiliki struktur atau opsi yang tidak dikenal.")
        job_id = entry.get("job_id")
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or job_id in seen_ids:
            raise ValueError("Identitas posting lama tidak valid atau duplikat.")
        seen_ids.add(job_id)
        account = _account_key(entry.get("account"))
        payload = {key: value for key, value in entry.items() if key in CONTENT_FIELDS}
        normalized = _normalized_payload(payload, directory, job_id)
        suffix = "-" + re.sub(r"[^A-Za-z0-9._:-]", "_", entry["account"].strip())
        base = job_id[:-len(suffix)] if len(job_id) > len(suffix) and job_id.casefold().endswith(suffix.casefold()) else job_id
        key = (base, json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
        group = groups.get(key)
        if group is not None and account not in bindings[group["content_id"]]:
            bindings[group["content_id"]][account] = [job_id]
            group["name"] = base
            continue
        content_id = hashlib.sha256(("instagram-content-v2\0" + job_id).encode("utf-8")).hexdigest()[:32]
        if content_id in bindings:
            raise ValueError("Identitas migrasi konten bertabrakan; data lama tidak diubah.")
        group = {"content_id": content_id, "name": job_id, **deepcopy(payload)}
        contents.append(group)
        bindings[content_id] = {account: [job_id]}
        groups[key] = group
    used_names = set()
    for content in contents:
        base, name, counter = content["name"], content["name"], 1
        while name in used_names:
            counter += 1
            suffix = f"-{counter}"
            name = base[:128 - len(suffix)] + suffix
        content["name"] = name
        used_names.add(name)
    return {"version": 2, "contents": contents, "bindings": bindings}


def load_catalog(path: Path) -> dict:
    """Baca saja: v1 dimigrasikan di memori; media hilang tetap bisa diedit."""
    try:
        document = json.loads(read_state_text(path, encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return {"version": 2, "contents": [], "bindings": {}}
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Katalog konten rusak atau tidak dapat dibaca; perbaiki file tanpa menimpa riwayat.") from None
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise ValueError("Versi katalog konten tidak valid.")
    if document["version"] == 1:
        return _migrate_legacy(document, path.parent)
    if document["version"] != 2 or set(document) != {"version", "contents", "bindings"}:
        raise ValueError("Struktur katalog konten tidak dikenal.")
    validate_manifest({"version": document["version"], "contents": document.get("contents")}, path.parent, check_files=False)
    bindings = document["bindings"]
    _validate_bindings(bindings)
    return {"version": 2, "contents": document["contents"], "bindings": bindings}


def content_status(document: dict, ledger_jobs: dict) -> dict:
    """Percobaan yang belum pasti selalu mengalahkan riwayat pasangan yang berhasil."""
    statuses = {}
    for content_id, pairs in document["bindings"].items():
        for account, attempts in pairs.items():
            latest = blocked = None
            for job_id in attempts:
                record = ledger_jobs.get(job_id)
                if record is None:
                    continue
                if not isinstance(record, dict) or _account_key(record.get("account")) != account:
                    raise ValueError("Identitas posting bertabrakan dengan akun lain pada ledger; publikasi diblokir.")
                status = record.get("status")
                if status not in {"published", "submitting", "unknown_after_submit", "unknown"}:
                    raise ValueError("Status ledger tidak dikenal; publikasi diblokir.")
                latest = {"status": status, "job_id": job_id}
                if status != "published":
                    blocked = latest
            if latest is not None:
                statuses.setdefault(content_id, {})[account] = blocked or latest
    return statuses


def plan_run(document: dict, accounts: list[Account], content_ids: list[str], ledger_jobs: dict,
             repost: bool = False, *, directory: Path) -> tuple[dict, dict]:
    """Cadangkan identitas hanya untuk pasangan konten/akun yang dipilih dan layak."""
    if type(repost) is not bool:
        raise ValueError("Pilihan posting ulang harus true/false.")
    if not isinstance(accounts, list) or not accounts or any(not isinstance(account, Account) for account in accounts):
        raise ValueError("Pilih minimal satu akun tujuan.")
    account_keys = [_account_key(account.identifier) for account in accounts]
    if len(account_keys) != len(set(account_keys)):
        raise ValueError("Pilihan akun duplikat.")
    if (not isinstance(content_ids, list) or not content_ids
            or any(not isinstance(value, str) or not CONTENT_ID.fullmatch(value) for value in content_ids)
            or len(content_ids) != len(set(content_ids))):
        raise ValueError("Pilih daftar konten yang tidak kosong dan tidak duplikat.")
    indexed = {content["content_id"]: content for content in document["contents"]}
    if any(content_id not in indexed for content_id in content_ids):
        raise ValueError("Konten pilihan tidak ditemukan; muat ulang daftar konten.")
    _validate_bindings(document["bindings"])
    statuses = content_status(document, ledger_jobs)
    for content_id in content_ids:
        for account in account_keys:
            status = statuses.get(content_id, {}).get(account)
            if status and status["status"] != "published":
                raise ValueError(f"Posting {indexed[content_id]['name']} untuk {account} belum pasti; periksa hasil dan ledger, jangan posting ulang.")
    planned = deepcopy(document)
    reserved = {job_id for pairs in planned["bindings"].values() for attempts in pairs.values() for job_id in attempts}
    passwords = {account.password for account in accounts if account.password}
    jobs = []
    for content_id in content_ids:
        eligible = [(account, key) for account, key in zip(accounts, account_keys)
                    if repost or statuses.get(content_id, {}).get(key, {}).get("status") != "published"]
        if not eligible:
            continue
        content = indexed[content_id]
        payload = _payload(content)
        parsed = parse_content(payload, directory, content["name"])
        resolved = deepcopy(payload)
        for item, media in zip(resolved["media"], parsed["media"]):
            item["path"] = str(media.path)
        if parsed["cover_path"] is not None:
            resolved["cover_path"] = str(parsed["cover_path"])
        for account, account_key in eligible:
            attempts = planned["bindings"].setdefault(content_id, {}).setdefault(account_key, [])
            if attempts and attempts[-1] not in ledger_jobs:
                job_id = attempts[-1]
            else:
                base = "content-" + hashlib.sha256((content_id + "\0" + account_key).encode("utf-8")).hexdigest()
                job_id = base if not attempts else f"{base}:r{len(attempts)}"
                if job_id in reserved or job_id in ledger_jobs:
                    raise ValueError("Identitas posting baru bertabrakan dengan riwayat; publikasi diblokir.")
                attempts.append(job_id)
                reserved.add(job_id)
            if any(password in job_id for password in passwords):
                raise ValueError("Identitas posting tidak boleh memuat kredensial akun.")
            jobs.append({"job_id": job_id, "account": account.identifier, **deepcopy(resolved)})
    if not jobs:
        raise ValueError("Semua pasangan konten dan akun sudah terposting; pilih konten baru atau aktifkan posting ulang secara eksplisit.")
    return planned, {"version": 1, "jobs": jobs}
