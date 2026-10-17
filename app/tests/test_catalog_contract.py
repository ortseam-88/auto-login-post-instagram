# create by Ortseam
"""Katalog v2, scope run, migrasi dan ledger; filesystem temp, tanpa browser/child nyata."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import Account
import config
from jobs import digest_job, load_jobs
import main
from models import PublishResult
from results import RunStore, account_inventory_lock
import ui_backend
import ui_server


class InventoryChild:
    """Only the external child is replaced; its inventory lease and snapshot are real."""

    def __init__(self, command, *, ready=b"ready\n"):
        self.command = list(command)
        self.returncode = None
        self.exited = threading.Event()
        self.ready_path = Path(command[command.index("--inventory-ready-file") + 1])
        self.snapshot_path = Path(command[command.index("--jobs") + 1]) if "--jobs" in command else None
        self.document = json.loads(self.snapshot_path.read_text(encoding="utf-8")) if self.snapshot_path else None
        self.inventory = account_inventory_lock()
        self.inventory.__enter__()
        self.ready_path.write_bytes(ready)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode

    def finish(self, code=0):
        if self.returncode is None:
            self.inventory.__exit__(None, None, None)
            self.returncode = code
            self.exited.set()

    def kill(self):
        self.finish(-9)


class CatalogContractTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ig-catalog-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / "input"
        self.media = self.input / "media"
        self.media.mkdir(parents=True)
        for name in ("a.jpg", "b.jpg", "cover.jpg", "clip.mp4"):
            (self.media / name).write_bytes(f"fixture {name}; never for Instagram".encode())
        self.accounts_file = self.input / "akun.txt"
        self.accounts_file.write_text("Uji_A|test-only\nuji_b|test-only\nuji_c|test-only\n", encoding="utf-8")
        self.accounts = [Account("Uji_A", "test-only"), Account("uji_b", "test-only"), Account("uji_c", "test-only")]
        self.manifest = self.input / "jobs.json"
        paths = patch.multiple(config,
            INPUT_ACCOUNTS_FILE=self.accounts_file,
            UI_JOBS_FILE=self.manifest,
            UI_MEDIA_DIR=self.media,
            UI_SETTINGS_FILE=self.input / "settings.json",
            UI_MEDIA_TRASH_DIR=self.root / "trash",
            UI_RUN_HISTORY_FILE=self.root / "output" / "history.json",
            RESULT_FILE=self.root / "output" / "results.json",
            LEDGER_FILE=self.root / "output" / "ledger.json",
            OUTPUT_DIR=self.root / "output",
            LOG_FILE=self.root / "logs" / "instagram.log",
        )
        paths.start()
        self.addCleanup(paths.stop)
        sessions = patch.object(config, "session_root", return_value=self.root / "profiles")
        sessions.start()
        self.addCleanup(sessions.stop)
        self.forbidden_child = Mock(side_effect=AssertionError("Test attempted to launch a real child"))
        process_guard = patch.object(ui_backend.subprocess, "Popen", self.forbidden_child)
        process_guard.start()
        self.addCleanup(process_guard.stop)
        self.children = []
        self.addCleanup(self.finish_children)
        self.manager = ui_backend.RunManager()
        self.settings = {"mode": "publish", "workers": 2, "manual_timeout": 0, "headless": True}
        self.next_log = ""
        self.addCleanup(self.close_fixture_log)

    def close_fixture_log(self):
        logger = logging.getLogger("instagram")
        for handler in logger.handlers[:]:
            if getattr(handler, "baseFilename", None) == str(config.LOG_FILE):
                logger.removeHandler(handler)
                handler.close()

    def content(self, *, content_id="a" * 32, name="kampanye", **payload):
        return {"content_id": content_id, "name": name, "kind": "photo",
                "media": [{"path": "media/a.jpg"}], **payload}

    def write_catalog(self, contents, bindings=None):
        self.manifest.write_text(json.dumps({"version": 2, "contents": contents, "bindings": bindings or {}}), encoding="utf-8")

    def disk_catalog(self):
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def popen(self, command, **kwargs):
        if self.next_log:
            kwargs["stdout"].write(self.next_log)
            kwargs["stdout"].flush()
        child = InventoryChild(command)
        self.children.append(child)
        return child

    def start(self, accounts, contents=None, *, repost=False, mode="publish"):
        with patch.object(ui_backend.subprocess, "Popen", side_effect=self.popen):
            status = self.manager.start({**self.settings, "mode": mode}, accounts, contents, repost)
        return status, self.children[-1]

    def finish_children(self):
        for child in self.children:
            child.finish()
        if self.children:
            self.manager.status()
        for child in self.children:
            if child.snapshot_path:
                self.wait_removed(child.snapshot_path)

    def wait_removed(self, path):
        deadline = time.monotonic() + 3
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(path.exists(), f"Snapshot remains after child exit: {path.name}")

    def parsed(self, child):
        return load_jobs(child.snapshot_path, self.accounts)

    def mark_published(self, job):
        digest = digest_job(job)
        with RunStore([]) as store:
            store.mark_submitting(job.job_id, digest, job.account)
            store.finish(job.job_id, digest, job.account, PublishResult("published", "fixture confirmed"))

    def write_ledger(self, entries):
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        rows = {job_id: {"digest": "a" * 64, "account": account, "status": status, "detail": "fixture",
                         "permalink": None, "updated_at": "2026-01-01T00:00:00+00:00"}
                for job_id, account, status in entries}
        config.LEDGER_FILE.write_text(json.dumps({"version": 1, "jobs": rows}), encoding="utf-8")

    def test_missing_catalog_and_save_without_any_accounts(self):
        self.accounts_file.unlink()
        self.assertEqual(ui_backend.read_manifest(), {"version": 2, "contents": []})
        content = self.content(caption="Disiapkan tanpa akun", comments_enabled=False,
                               media=[{"path": "media/a.jpg", "alt_text": "Deskripsi foto",
                                       "people_tags": [{"username": "friend", "x": 0.25, "y": 0.75}]}])
        self.assertEqual(ui_backend.save_manifest({"version": 2, "contents": [content]}), [content["content_id"]])
        self.assertEqual(ui_backend.read_manifest(), {"version": 2, "contents": [content]})
        self.assertEqual(self.disk_catalog()["bindings"], {})
        self.assertFalse(self.accounts_file.exists())
        self.forbidden_child.assert_not_called()

    def test_save_rejects_execution_shape_and_client_owned_bindings_atomically(self):
        content = self.content()
        self.write_catalog([content], {content["content_id"]: {"uji_a": ["kept"]}})
        original = self.manifest.read_bytes()
        invalid = [
            {"version": 1, "jobs": []},
            {"version": 2, "contents": [content], "bindings": {}},
            {"version": 2, "contents": [{**content, "account": "Uji_A"}]},
            {"version": 2, "contents": [{**content, "accounts": ["Uji_A"]}]},
            {"version": 2, "contents": [content, {**content, "content_id": "b" * 32}]},
            {"version": 2, "contents": [{**content, "media": []}]},
        ]
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ValueError):
                ui_backend.save_manifest(document)
            self.assertEqual(self.manifest.read_bytes(), original)

    def test_migration_groups_only_equal_nested_payload_and_distinct_accounts(self):
        payload = {"kind": "carousel", "caption": "Tetap lengkap", "location": "Jakarta",
                   "collaborators": ["Friend"], "comments_enabled": False, "hide_like_view_counts": True,
                   "media": [{"path": "media/a.jpg", "alt_text": "Awal", "crop": "1:1", "zoom": 25,
                              "adjustments": {"brightness": 4, "contrast": -3},
                              "people_tags": [{"username": "Friend", "x": 0.25, "y": 0.75}]},
                             {"path": "media/b.jpg", "alt_text": "Akhir", "crop": "1:1"}]}
        equivalent = deepcopy(payload)
        equivalent["collaborators"] = ["friend"]
        equivalent["media"][0]["people_tags"][0]["username"] = "friend"
        equivalent["media"][0]["adjustments"] = {"contrast": -3, "brightness": 4}
        distinct = deepcopy(payload)
        distinct["media"][0]["people_tags"][0]["x"] = 0.75
        jobs = [
            {"job_id": "kampanye-uji_a", "account": " UJI_A ", **payload},
            {"job_id": "kampanye-uji_b", "account": "uji_b", **equivalent},
            {"job_id": "kampanye", "account": "Uji_A", **payload},
            {"job_id": "kampanye-uji_c", "account": "uji_c", **distinct},
        ]
        original = ("\ufeff" + json.dumps({"version": 1, "jobs": jobs}, ensure_ascii=False, indent=3) + "\n").encode("utf-8")
        self.manifest.write_bytes(original)
        view = ui_backend.read_manifest()
        self.assertEqual(ui_backend.read_manifest(), view)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertFalse(self.manifest.with_name("jobs.v1-backup.json").exists())
        self.assertEqual(len(view["contents"]), 3)
        self.assertEqual(len({item["content_id"] for item in view["contents"]}), 3)
        self.assertEqual(len({item["name"] for item in view["contents"]}), 3)
        for item in view["contents"]:
            self.assertRegex(item["content_id"], r"^[0-9a-f]{32}$")
            self.assertNotIn("account", item)
            self.assertNotIn("job_id", item)
        self.assertEqual(view["contents"][0]["media"], payload["media"])
        self.assertEqual(view["contents"][2]["media"], distinct["media"])
        ui_backend.save_manifest(view)
        bindings = self.disk_catalog()["bindings"]
        self.assertEqual(bindings[view["contents"][0]["content_id"]], {"uji_a": ["kampanye-uji_a"], "uji_b": ["kampanye-uji_b"]})
        self.assertEqual(bindings[view["contents"][1]["content_id"]], {"uji_a": ["kampanye"]})
        self.assertEqual(bindings[view["contents"][2]["content_id"]], {"uji_c": ["kampanye-uji_c"]})
        backup = self.manifest.with_name("jobs.v1-backup.json")
        self.assertEqual(backup.read_bytes(), original)
        view["contents"][0]["name"] = "nama-baru"
        ui_backend.save_manifest(view)
        self.assertEqual(self.disk_catalog()["bindings"], bindings)
        self.assertEqual(backup.read_bytes(), original)

    def test_start_migration_keeps_legacy_ids_digests_and_absolute_media_cover(self):
        jobs = [
            {"job_id": "foto-uji_a", "account": "Uji_A", "kind": "photo", "caption": "Arsip",
             "media": [{"path": "media/a.jpg", "alt_text": "Foto", "adjustments": {"contrast": 12}}]},
            {"job_id": "video-uji_a", "account": "uji_a", "kind": "reel", "caption": "Video",
             "media": [{"path": "media/clip.mp4", "crop": "9:16"}], "cover_path": "media/cover.jpg",
             "trim_start": 1, "trim_end": 4, "keep_original_audio": False,
             "people_tags": [{"username": "friend"}], "ai_label": True},
        ]
        self.manifest.write_text(json.dumps({"version": 1, "jobs": jobs}), encoding="utf-8")
        original = self.manifest.read_bytes()
        before = {job.job_id: digest_job(job) for job in load_jobs(self.manifest, self.accounts)}
        ids = [item["content_id"] for item in ui_backend.read_manifest()["contents"]]
        status, child = self.start(["uji_a"], ids)
        generated = self.parsed(child)
        self.assertEqual({job.job_id: digest_job(job) for job in generated}, before)
        self.assertEqual(status["accounts"], ["Uji_A"])
        self.assertEqual(status["contents"], ids)
        for raw in child.document["jobs"]:
            self.assertTrue(Path(raw["media"][0]["path"]).is_absolute())
            self.assertTrue(Path(raw["media"][0]["path"]).is_file())
        self.assertTrue(generated[1].cover_path.samefile(self.media / "cover.jpg"))
        self.assertTrue(Path(child.document["jobs"][1]["cover_path"]).is_absolute())
        self.assertEqual(self.manifest.with_name("jobs.v1-backup.json").read_bytes(), original)
        child.finish()
        self.manager.status()
        renamed = ui_backend.read_manifest()
        renamed["contents"][0]["name"] = "nama-tidak-mengubah-identitas"
        ui_backend.save_manifest(renamed)
        _, retry = self.start(["Uji_A"], ids)
        self.assertEqual({job.job_id: digest_job(job) for job in self.parsed(retry)}, before)

    def test_catalog_removal_keeps_tombstones_and_protects_media_and_cover(self):
        photo = self.content()
        reel = self.content(content_id="b" * 32, name="video", kind="reel",
                            media=[{"path": "media/clip.mp4"}], cover_path="media/cover.jpg")
        bindings = {photo["content_id"]: {"uji_a": ["legacy-a"]}, "f" * 32: {"deleted-account": ["tombstone"]}}
        self.write_catalog([photo, reel], bindings)
        original = self.manifest.read_bytes()
        for name in ("a.jpg", "cover.jpg"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ui_backend.remove_media(name)
            self.assertTrue((self.media / name).is_file())
            self.assertEqual(self.manifest.read_bytes(), original)
        ui_backend.save_manifest({"version": 2, "contents": []})
        self.assertEqual(self.disk_catalog()["bindings"], bindings)
        ui_backend.remove_media("cover.jpg")
        self.assertFalse((self.media / "cover.jpg").exists())
        self.assertEqual(self.disk_catalog()["bindings"], bindings)

    def test_import_accepts_quoted_explorer_paths_with_spaces(self):
        directory = self.root / "sumber media"
        directory.mkdir()
        first, second = directory / "foto satu.jpg", directory / "foto dua.jpg"
        first.write_bytes(b"foto-uji-satu")
        second.write_bytes(b"foto-uji-dua")
        self.assertEqual(ui_backend.import_media(f'  "{first}"  '),
                         {"name": first.name, "path": f"media/{first.name}"})
        self.assertEqual(ui_backend.import_media_batch([f'\t"{second}"\r\n']),
                         [{"name": second.name, "path": f"media/{second.name}"}])
        self.assertEqual((self.media / first.name).read_bytes(), first.read_bytes())
        self.assertEqual((self.media / second.name).read_bytes(), second.read_bytes())

    def test_failed_batch_import_cannot_leave_saved_reference_to_rolled_back_media(self):
        content = self.content()
        self.write_catalog([content])
        original = self.manifest.read_bytes()
        media_before = {path.name: path.read_bytes() for path in self.media.iterdir()}
        first, second = self.root / "impor-a.jpg", self.root / "impor-b.jpg"
        first.write_bytes(b"media-uji-a")
        second.write_bytes(b"media-uji-b")
        candidate = {**content, "media": [{"path": "media/impor-a.jpg"}]}
        copying_second = threading.Event()
        fail_copy = threading.Event()
        failures = []
        copy_file = ui_backend.shutil.copy2

        def pause_then_fail(source, destination, *args, **kwargs):
            if Path(source).samefile(second):
                copying_second.set()
                if not fail_copy.wait(5):
                    raise TimeoutError("Izin kegagalan salin tidak diterima.")
                raise OSError("Salinan kedua gagal terkontrol.")
            return copy_file(source, destination, *args, **kwargs)

        def import_batch():
            try:
                ui_backend.import_media_batch([str(self.media / "a.jpg"), str(first), str(second)])
            except BaseException as exc:
                failures.append(exc)

        with patch.object(ui_backend.shutil, "copy2", side_effect=pause_then_fail):
            worker = threading.Thread(target=import_batch, daemon=True)
            worker.start()
            try:
                self.assertTrue(copying_second.wait(5), "Salinan kedua tidak dimulai.")
                self.assertEqual((self.media / first.name).read_bytes(), first.read_bytes())
                with self.assertRaises(ValueError):
                    ui_backend.save_manifest({"version": 2, "contents": [candidate]})
                with self.assertRaises(ValueError):
                    ui_backend.remove_media(first.name)
                with self.assertRaises(ValueError):
                    self.manager.start(self.settings, ["Uji_A"], [content["content_id"]])
                self.assertEqual(self.manifest.read_bytes(), original)
            finally:
                fail_copy.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "Impor belum berhenti setelah kegagalan salin.")
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], OSError)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual({path.name: path.read_bytes() for path in self.media.iterdir()}, media_before)
        self.forbidden_child.assert_not_called()
        with self.assertRaises(ValueError):
            ui_backend.save_manifest({"version": 2, "contents": [candidate]})
        self.assertEqual(ui_backend.import_media(str(first)), {"name": first.name, "path": f"media/{first.name}"})
        self.assertEqual(ui_backend.save_manifest({"version": 2, "contents": [candidate]}), [content["content_id"]])
        self.assertEqual(self.disk_catalog()["contents"], [candidate])
        self.assertEqual((self.media / first.name).read_bytes(), first.read_bytes())

    def test_multiple_accounts_and_contents_generate_only_requested_product(self):
        contents = [self.content(content_id=f"{index + 1:032x}", name=f"konten-{index}", caption=f"caption-{index}") for index in range(3)]
        self.write_catalog(contents)
        wanted = [contents[2]["content_id"], contents[0]["content_id"]]
        status, child = self.start(["UJI_C", "uji_a"], wanted)
        jobs = self.parsed(child)
        self.assertEqual({(job.account, job.caption) for job in jobs}, {
            ("uji_c", "caption-2"), ("uji_c", "caption-0"), ("Uji_A", "caption-2"), ("Uji_A", "caption-0"),
        })
        self.assertEqual(len(jobs), 4)
        self.assertEqual(len({job.job_id for job in jobs}), 4)
        selected_accounts = [child.command[index + 1] for index, value in enumerate(child.command) if value == "--account"]
        self.assertEqual(set(selected_accounts), {"uji_c", "Uji_A"})
        self.assertEqual(set(status["accounts"]), {"uji_c", "Uji_A"})
        self.assertEqual(set(status["contents"]), set(wanted))
        self.assertEqual(set(status["content_names"]), {"konten-2", "konten-0"})
        self.assertEqual(status["posting_count"], 4)
        self.assertNotEqual(child.snapshot_path, self.manifest)
        bindings = self.disk_catalog()["bindings"]
        self.assertEqual(set(bindings), set(wanted))
        self.assertEqual({account for pairs in bindings.values() for account in pairs}, {"uji_a", "uji_c"})
        child.finish()
        finished = self.manager.status()
        self.assertFalse(finished["running"])
        history = json.loads(config.UI_RUN_HISTORY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(history[-1]["contents"], wanted)
        self.assertEqual(set(history[-1]["accounts"]), {"uji_c", "Uji_A"})
        self.assertNotIn("account", history[-1])

    def test_invalid_direct_selection_never_broadens_or_changes_log(self):
        content = self.content()
        self.write_catalog([content])
        before = self.manifest.read_bytes()
        log = config.LOG_FILE.parent / "ui-run.log"
        log.parent.mkdir(parents=True)
        log.write_text("previous useful log", encoding="utf-8")
        invalid = [
            ("publish", None, [content["content_id"]], False),
            ("publish", [], [content["content_id"]], False),
            ("publish", "Uji_A", [content["content_id"]], False),
            ("publish", ["missing"], [content["content_id"]], False),
            ("publish", ["uji_a", "UJI_A"], [content["content_id"]], False),
            ("publish", ["Uji_A"], None, False),
            ("publish", ["Uji_A"], [], False),
            ("publish", ["Uji_A"], content["content_id"], False),
            ("publish", ["Uji_A"], ["f" * 32], False),
            ("publish", ["Uji_A"], [content["content_id"]] * 2, False),
            ("publish", ["Uji_A"], [content["content_id"]], "true"),
            ("login", [], None, False),
            ("login", ["Uji_A"], [content["content_id"]], False),
            ("login", ["Uji_A"], None, True),
        ]
        for mode, accounts, contents, repost in invalid:
            with self.subTest(mode=mode, accounts=accounts, contents=contents, repost=repost), self.assertRaises(ValueError):
                self.manager.start({**self.settings, "mode": mode}, accounts, contents, repost)
            self.assertEqual(log.read_text(encoding="utf-8"), "previous useful log")
            self.assertEqual(self.manifest.read_bytes(), before)
        self.forbidden_child.assert_not_called()

    def test_partial_published_skips_by_default_and_repost_reuses_unsubmitted_attempt(self):
        content = self.content()
        self.write_catalog([content])
        _, first = self.start(["Uji_A"], [content["content_id"]])
        original_job = self.parsed(first)[0]
        first.finish()
        self.manager.status()
        self.mark_published(original_job)
        ledger = config.LEDGER_FILE.read_bytes()
        status, partial = self.start(["Uji_A", "uji_b"], [content["content_id"]])
        fresh_job = self.parsed(partial)[0]
        self.assertEqual([(job.account, job.job_id) for job in self.parsed(partial)], [("uji_b", fresh_job.job_id)])
        self.assertEqual(status["posting_count"], 1)
        self.assertEqual(status["content_status"][content["content_id"]]["uji_a"],
                         {"status": "published", "job_id": original_job.job_id})
        partial.finish()
        self.manager.status()
        with self.assertRaises(ValueError):
            self.manager.start(self.settings, ["Uji_A"], [content["content_id"]])
        _, repost = self.start(["Uji_A", "uji_b"], [content["content_id"]], repost=True)
        attempts = {job.account: job.job_id for job in self.parsed(repost)}
        self.assertNotEqual(attempts["Uji_A"], original_job.job_id)
        self.assertEqual(attempts["uji_b"], fresh_job.job_id)
        self.assertEqual(self.disk_catalog()["bindings"][content["content_id"]], {
            "uji_a": [original_job.job_id, attempts["Uji_A"]], "uji_b": [fresh_job.job_id],
        })
        repost.finish()
        self.manager.status()
        _, retry = self.start(["Uji_A", "uji_b"], [content["content_id"]], repost=True)
        self.assertEqual({job.account: job.job_id for job in self.parsed(retry)}, attempts)
        self.assertEqual(self.disk_catalog()["contents"], [content])
        self.assertEqual(config.LEDGER_FILE.read_bytes(), ledger)

    def test_any_uncertain_attempt_blocks_entire_selection_even_after_newer_published(self):
        content = self.content()
        bindings = {content["content_id"]: {"uji_a": ["uncertain", "confirmed-later"]}}
        self.write_catalog([content], bindings)
        self.write_ledger([("uncertain", "Uji_A", "unknown_after_submit"), ("confirmed-later", "Uji_A", "published")])
        catalog_before, ledger_before = self.manifest.read_bytes(), config.LEDGER_FILE.read_bytes()
        status = self.manager.status()["content_status"][content["content_id"]]["uji_a"]
        self.assertEqual(status, {"status": "unknown_after_submit", "job_id": "uncertain"})
        for repost in (False, True):
            with self.subTest(repost=repost), self.assertRaises(ValueError):
                self.manager.start(self.settings, ["Uji_A", "uji_b"], [content["content_id"]], repost)
            self.assertEqual(self.manifest.read_bytes(), catalog_before)
            self.assertEqual(config.LEDGER_FILE.read_bytes(), ledger_before)
        self.forbidden_child.assert_not_called()

    def test_repost_id_collision_cannot_bypass_unrelated_uncertain_ledger(self):
        content = self.content()
        self.write_catalog([content])
        _, first = self.start(["Uji_A"], [content["content_id"]])
        base_job = self.parsed(first)[0]
        first.finish()
        self.manager.status()
        self.mark_published(base_job)
        before_repost = self.manifest.read_bytes()
        _, planned = self.start(["Uji_A"], [content["content_id"]], repost=True)
        next_id = self.parsed(planned)[0].job_id
        planned.finish()
        self.manager.status()
        self.manifest.write_bytes(before_repost)
        ledger = json.loads(config.LEDGER_FILE.read_text(encoding="utf-8"))
        ledger["jobs"][next_id] = {**ledger["jobs"][base_job.job_id], "account": "uji_b", "status": "submitting"}
        config.LEDGER_FILE.write_text(json.dumps(ledger), encoding="utf-8")
        ledger_before = config.LEDGER_FILE.read_bytes()
        with self.assertRaises(ValueError):
            self.manager.start(self.settings, ["Uji_A"], [content["content_id"]], True)
        self.assertEqual(self.manifest.read_bytes(), before_repost)
        self.assertEqual(config.LEDGER_FILE.read_bytes(), ledger_before)

    def test_startup_handoff_retains_locks_and_snapshot_until_actual_child_exit(self):
        content = self.content()
        self.write_catalog([content])
        accounts_before = self.accounts_file.read_bytes()

        def during_start(command, **kwargs):
            with self.assertRaises(ValueError):
                ui_backend.remove_account("uji_b")
            self.assertEqual(self.accounts_file.read_bytes(), accounts_before)
            return self.popen(command, **kwargs)

        with patch.object(ui_backend.subprocess, "Popen", side_effect=during_start):
            self.manager.start(self.settings, ["Uji_A", "uji_b"], [content["content_id"]])
        child = self.children[-1]
        self.assertTrue(child.snapshot_path.is_file())
        self.assertFalse(child.ready_path.exists())
        source = self.root / "terkunci.jpg"
        source.write_bytes(b"media-uji-terkunci")
        for mutation in (lambda: ui_backend.remove_account("uji_b"),
                         lambda: ui_backend.save_manifest({"version": 2, "contents": []}),
                         lambda: ui_backend.remove_media("b.jpg"),
                         lambda: ui_backend.import_media(str(source)),
                         lambda: ui_backend.import_media_batch([str(source)])):
            with self.assertRaises(ValueError):
                mutation()
        self.assertFalse((self.media / source.name).exists())
        lock_paths = [config.OUTPUT_DIR / name for name in (".instagram-startup.lock", ".instagram-accounts.lock")]
        before_inodes = [path.stat().st_ino for path in lock_paths]
        child.finish()
        self.wait_removed(child.snapshot_path)
        ui_backend.remove_account("uji_b")
        self.assertEqual([path.stat().st_ino for path in lock_paths], before_inodes)
        self.assertNotIn("uji_b|", self.accounts_file.read_text(encoding="utf-8"))

    def test_partial_ready_marker_and_spawn_error_fail_closed_and_clean_snapshots(self):
        content = self.content()
        self.write_catalog([content])
        attempted = []

        def exited_before_ready(command, **kwargs):
            child = InventoryChild(command, ready=b"ready")
            self.children.append(child)
            attempted.append(child.snapshot_path)
            child.finish()
            return child

        with patch.object(ui_backend.subprocess, "Popen", side_effect=exited_before_ready), self.assertRaises(ValueError):
            self.manager.start(self.settings, ["Uji_A"], [content["content_id"]])
        self.assertFalse(self.children[-1].ready_path.exists())
        self.wait_removed(attempted[-1])
        reserved = self.disk_catalog()["bindings"]

        def spawn_failure(command, **kwargs):
            attempted.append(Path(command[command.index("--jobs") + 1]))
            raise OSError("fixture spawn failure")

        with patch.object(ui_backend.subprocess, "Popen", side_effect=spawn_failure), self.assertRaises((OSError, ValueError)):
            self.manager.start(self.settings, ["Uji_A"], [content["content_id"]])
        self.wait_removed(attempted[-1])
        self.assertEqual(self.disk_catalog()["bindings"], reserved)
        _, retry = self.start(["Uji_A"], [content["content_id"]])
        self.assertEqual([job.job_id for job in self.parsed(retry)], reserved[content["content_id"]]["uji_a"])

    def test_finished_run_history_is_recorded_before_next_log_truncation(self):
        self.next_log = "unique first run evidence\n"
        _, first = self.start(["Uji_A"], mode="login")
        self.assertNotIn("--jobs", first.command)
        first.finish()
        self.next_log = "second run evidence\n"
        status, second = self.start(["uji_b"], mode="login")
        history = json.loads(config.UI_RUN_HISTORY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(history[0]["accounts"], ["Uji_A"])
        self.assertIn("unique first run evidence", history[0]["log_tail"])
        self.assertNotIn("second run evidence", history[0]["log_tail"])
        self.assertEqual(status["accounts"], ["uji_b"])
        self.assertEqual([second.command[index + 1] for index, value in enumerate(second.command) if value == "--account"], ["uji_b"])

    def serve(self):
        server = ui_server.create_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        self.origin = f"http://127.0.0.1:{server.server_address[1]}"
        self.token = server.token

    def request(self, path, data=None):
        request = Request(self.origin + path, data=None if data is None else json.dumps(data).encode("utf-8"),
                          headers={"X-UI-Token": self.token, "Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_account_api_rejects_nontext_fields_without_mutating_inventory(self):
        self.accounts_file.write_bytes(self.accounts_file.read_bytes() + b"None|sandi-uji\n['uji_daftar']|sandi-uji\n")
        self.write_catalog([self.content()])
        accounts_before, catalog_before = self.accounts_file.read_bytes(), self.manifest.read_bytes()
        self.serve()
        invalid = (
            ("/api/accounts", {"identifier": "uji_null", "password": None}),
            ("/api/accounts", {"identifier": "uji_objek", "password": {"nilai": "sandi-uji"}}),
            ("/api/accounts", {"identifier": None, "password": "sandi-uji"}),
            ("/api/accounts", {"identifier": ["uji_baru"], "password": "sandi-uji"}),
            ("/api/accounts/bulk", {"text": ["uji_bulk|sandi-uji"]}),
            ("/api/accounts/delete", {"identifier": None}),
            ("/api/accounts/delete", {"identifier": ["uji_daftar"]}),
        )
        for path, body in invalid:
            with self.subTest(path=path, body=body):
                status, response = self.request(path, body)
                self.assertEqual(status, 400)
                self.assertIn("error", response)
                self.assertEqual(self.accounts_file.read_bytes(), accounts_before)
                self.assertEqual(self.manifest.read_bytes(), catalog_before)
        self.forbidden_child.assert_not_called()

    def test_api_account_free_save_and_explicit_invalid_run_boundaries(self):
        self.accounts_file.write_text("", encoding="utf-8")
        self.serve()
        content = self.content()
        status, saved = self.request("/api/jobs", {"manifest": {"version": 2, "contents": [content]}})
        self.assertEqual((status, saved), (200, {"ok": True, "content_ids": [content["content_id"]]}))
        status, state = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(state["manifest"], {"version": 2, "contents": [content]})
        self.assertEqual(state["accounts"], [])
        self.accounts_file.write_text("Uji_A|test-only\n", encoding="utf-8")
        original = self.manifest.read_bytes()
        valid = {"settings": self.settings, "accounts": ["Uji_A"], "contents": [content["content_id"]], "confirm": "PUBLISH"}
        invalid = [
            {key: value for key, value in valid.items() if key != "accounts"},
            {key: value for key, value in valid.items() if key != "contents"},
            {**valid, "accounts": []},
            {**valid, "contents": []},
            {**valid, "accounts": ["unknown"]},
            {**valid, "contents": ["f" * 32]},
            {**valid, "accounts": ["Uji_A", "uji_a"]},
            {**valid, "contents": [content["content_id"]] * 2},
            {**valid, "account": "Uji_A"},
            {**valid, "jobs": ["old-id"]},
            {**valid, "confirm": "CANCEL"},
            {**valid, "repost": "true"},
            {**valid, "settings": {**self.settings, "mode": "login"}},
        ]
        for body in invalid:
            with self.subTest(body=body):
                status, response = self.request("/api/run/start", body)
                self.assertEqual(status, 400)
                self.assertIn("error", response)
                self.assertEqual(self.manifest.read_bytes(), original)
        self.forbidden_child.assert_not_called()

    def test_run_rejects_changed_confirmation_before_writes_and_accepts_current_contents(self):
        reviewed = self.content(caption="Isi yang sudah diperiksa")
        current = {**reviewed, "caption": "Isi diubah tab lain", "media": [{"path": "media/b.jpg"}]}
        self.write_catalog([current])
        catalog_before = self.manifest.read_bytes()
        log = config.LOG_FILE.parent / "ui-run.log"
        log.parent.mkdir(parents=True)
        log.write_text("Log sebelumnya tetap utuh.", encoding="utf-8")
        self.serve()
        valid = {"settings": self.settings, "accounts": ["Uji_A"], "contents": [current["content_id"]],
                 "confirm": "PUBLISH", "expected_contents": [current]}
        invalid = (
            {**valid, "expected_contents": [reviewed]},
            {**valid, "expected_contents": current},
            {**valid, "expected_contents": []},
            {**valid, "settings": {**self.settings, "mode": "login"}, "contents": []},
        )
        for body in invalid:
            with self.subTest(body=body):
                status, response = self.request("/api/run/start", body)
                self.assertEqual(status, 400)
                self.assertIn("error", response)
                self.assertEqual(self.manifest.read_bytes(), catalog_before)
                self.assertEqual(log.read_text(encoding="utf-8"), "Log sebelumnya tetap utuh.")
                self.assertFalse(config.LEDGER_FILE.exists())
                self.assertFalse(config.UI_RUN_HISTORY_FILE.exists())
        self.forbidden_child.assert_not_called()
        with patch.object(ui_backend.subprocess, "Popen", side_effect=self.popen):
            self.manager.start(self.settings, ["Uji_A"], [current["content_id"]], expected_contents=[current])
        jobs = self.parsed(self.children[-1])
        self.assertEqual([(job.account, job.caption) for job in jobs], [("Uji_A", current["caption"])])
        self.assertEqual(self.disk_catalog()["contents"], [current])

    def test_cli_repeated_account_filter_is_exact_and_unknown_never_reaches_engine(self):
        run = AsyncMock(return_value=0)
        with patch.object(main, "_run", run):
            code = main.main(["--accounts", str(self.accounts_file), "--account", "UJI_C", "--account", "uji_a", "--mode", "login"])
        self.assertEqual(code, 0)
        self.assertEqual({account.identifier for account in run.call_args.args[1]}, {"Uji_A", "uji_c"})
        run.reset_mock()
        with patch.object(main, "_run", run):
            code = main.main(["--accounts", str(self.accounts_file), "--account", "unknown", "--mode", "login"])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_stop_acknowledgement_keeps_run_active_until_child_exit(self):
        content = self.content()
        self.write_catalog([content])
        _, child = self.start(["Uji_A"], [content["content_id"]])
        child.send_signal = Mock()
        stopping = self.manager.stop()
        self.assertTrue(stopping["running"])
        self.assertTrue(stopping["stopping"])
        self.assertIsNone(stopping["exit_code"])
        self.assertTrue(child.snapshot_path.exists())
        with self.assertRaises(ValueError):
            ui_backend.remove_account("Uji_A")
        self.assertTrue(self.manager.stop()["stopping"])
        self.assertEqual(child.send_signal.call_count, 1)
        child.finish(130)
        stopped = self.manager.status()
        self.assertFalse(stopped["running"])
        self.assertFalse(stopped["stopping"])
        self.assertEqual(stopped["exit_code"], 130)
        self.assertFalse(child.snapshot_path.exists())
        next_run, next_child = self.start(["Uji_A"], mode="login")
        self.assertTrue(next_run["running"])
        self.assertFalse(next_run["stopping"])
        next_child.finish()


if __name__ == "__main__":
    unittest.main()
