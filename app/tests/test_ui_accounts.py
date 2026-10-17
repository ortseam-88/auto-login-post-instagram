# create by Ortseam
"""Hapus akun dan profil tanpa menghapus katalog/riwayat; profil terkunci menolak."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_session
import config
import ui_backend
from accounts import load_accounts
from login_flow import IDENTITY_FILE
from results import account_inventory_lock


class AccountRemovalTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ig-accounts-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.accounts_file = self.root / "akun.txt"
        self.accounts_file.write_text("# identifier|password\nhapus_a|test-only\nsisa_b|test-only\n", encoding="utf-8")
        (self.root / "photo.jpg").write_bytes(b"fixture-photo-not-for-browser")
        for patcher in (
            patch.object(config, "INPUT_ACCOUNTS_FILE", self.accounts_file),
            patch.object(config, "OUTPUT_DIR", self.root / "output"),
            patch.object(config, "UI_JOBS_FILE", self.root / "jobs.json"),
            patch.object(config, "session_root", return_value=self.root / "sessions"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def profile(self, identifier):
        profile = browser_session.profile_directory(identifier)
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_bytes(b"fixture-cookie-jar")
        (profile / IDENTITY_FILE).write_text('{"username": "fixture"}', encoding="utf-8")
        profile.with_suffix(".lock").write_bytes(b"\0")
        return profile

    def remaining(self):
        return [line.partition("|")[0] for line in self.accounts_file.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")]

    def test_removal_keeps_stable_profile_lock_and_deletes_account_line(self):
        profile = self.profile("hapus_a")
        self.assertEqual(ui_backend.remove_account("HAPUS_A"), {"profile_removed": True})
        self.assertFalse(profile.exists())
        self.assertTrue(profile.with_suffix(".lock").exists())
        self.assertEqual(self.remaining(), ["sisa_b"])
        rows, error = ui_backend.account_rows()
        self.assertIsNone(error)
        self.assertEqual([(row["identifier"], row["profile_exists"]) for row in rows], [("sisa_b", False)])

    def test_removal_without_profile_reports_nothing_removed(self):
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": False})
        self.assertEqual(self.remaining(), ["sisa_b"])

    def test_busy_profile_is_refused_and_account_kept(self):
        profile = self.profile("hapus_a")
        with browser_session._profile_lock(profile.with_suffix(".lock")):
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
        self.assertTrue((profile / IDENTITY_FILE).is_file())
        self.assertEqual(self.remaining(), ["hapus_a", "sisa_b"])
        # Setelah run selesai (kunci dilepas) penghapusan berjalan normal.
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": True})
        self.assertFalse(profile.exists())

    def test_other_account_profiles_are_untouched(self):
        removed = self.profile("hapus_a")
        kept = self.profile("sisa_b")
        ui_backend.remove_account("hapus_a")
        self.assertFalse(removed.exists())
        self.assertTrue((kept / IDENTITY_FILE).is_file())
        self.assertTrue(kept.with_suffix(".lock").is_file())

    def test_removal_preserves_catalog_and_retained_execution_bindings(self):
        profile = self.profile("hapus_a")
        content_id = "a" * 32
        document = {"version": 2, "contents": [{
            "content_id": content_id, "name": "saved-a", "kind": "photo",
            "media": [{"path": "photo.jpg"}],
        }], "bindings": {content_id: {"hapus_a": ["saved-a", "saved-a-repost"], "sisa_b": ["saved-b"]}}}
        config.UI_JOBS_FILE.write_text(json.dumps(document), encoding="utf-8")
        original = config.UI_JOBS_FILE.read_bytes()
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": True})
        self.assertEqual(self.remaining(), ["sisa_b"])
        self.assertFalse(profile.exists())
        self.assertTrue(profile.with_suffix(".lock").is_file())
        self.assertEqual(config.UI_JOBS_FILE.read_bytes(), original)

    def test_queued_account_cannot_be_deleted_during_run_inventory_lock(self):
        with account_inventory_lock():
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
        self.assertEqual(self.remaining(), ["hapus_a", "sisa_b"])

    def test_legacy_binding_does_not_prevent_account_removal(self):
        profile = self.profile("hapus_a")
        config.UI_JOBS_FILE.write_text(json.dumps({"version": 1, "jobs": [{
            "job_id": "saved-a", "account": " HAPUS_A ", "kind": "photo",
            "media": [{"path": "photo.jpg"}],
        }]}), encoding="utf-8")
        original = config.UI_JOBS_FILE.read_bytes()
        ui_backend.remove_account("hapus_a")
        self.assertEqual(self.remaining(), ["sisa_b"])
        self.assertFalse(profile.exists())
        self.assertEqual(config.UI_JOBS_FILE.read_bytes(), original)

    def test_failed_account_staging_preserves_profile_cookies(self):
        profile = self.profile("hapus_a")
        with patch.object(ui_backend.os, "fsync", side_effect=OSError("fixture disk full")):
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
        self.assertTrue((profile / "Default" / "Cookies").is_file())
        self.assertEqual(self.remaining(), ["hapus_a", "sisa_b"])

    def test_failed_profile_cleanup_keeps_account_retryable(self):
        profile = self.profile("hapus_a")
        original = b"\xef\xbb\xbf# fixture\r\nhapus_a|test-only\r\nsisa_b|test-only\r\n"
        self.accounts_file.write_bytes(original)

        def partial_delete(path):
            (path / IDENTITY_FILE).unlink()
            raise PermissionError("fixture profile still in use")

        with patch.object(ui_backend.shutil, "rmtree", side_effect=partial_delete):
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
        self.assertEqual(self.accounts_file.read_bytes(), original)
        self.assertTrue((profile / "Default" / "Cookies").is_file())
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": True})
        self.assertFalse(profile.exists())
        self.assertEqual(self.remaining(), ["sisa_b"])

    def test_failed_account_commit_keeps_deletion_retryable(self):
        profile = self.profile("hapus_a")
        original = self.accounts_file.read_bytes()
        with patch.object(ui_backend.os, "replace", side_effect=PermissionError("fixture account file in use")):
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
        self.assertEqual(self.accounts_file.read_bytes(), original)
        self.assertFalse(profile.exists())
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": False})
        self.assertEqual(self.remaining(), ["sisa_b"])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing boundary")
    def test_locked_cookie_file_keeps_account_until_profile_can_be_deleted(self):
        import ctypes
        from ctypes import wintypes

        profile = self.profile("hapus_a")
        original = self.accounts_file.read_bytes()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(profile / "Default" / "Cookies"), 0x80000000, 1, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with self.assertRaises(ValueError):
                ui_backend.remove_account("hapus_a")
            self.assertEqual(self.accounts_file.read_bytes(), original)
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(ui_backend.remove_account("hapus_a"), {"profile_removed": True})
        self.assertFalse(profile.exists())
        self.assertEqual(self.remaining(), ["sisa_b"])

    def test_account_rewrites_preserve_accepted_password_bytes(self):
        password = " kiri\u2028tengah\u2029\x85\v\f\x1c\x1d\x1eakhir "
        ui_backend.add_account("edge_a", password)
        expected = password.encode("utf-8")
        record = b"edge_a|" + expected
        self.assertEqual(load_accounts(self.accounts_file)[-1].password.encode("utf-8"), expected)
        mutations = (
            ("tambah satu", lambda: ui_backend.add_account("baru_satu", "sandi-uji")),
            ("tambah banyak", lambda: ui_backend.add_accounts_bulk("baru_dua|sandi-uji\r\nbaru_tiga|sandi-uji\r")),
            ("hapus lainnya", lambda: ui_backend.remove_account("hapus_a")),
        )
        for label, mutate in mutations:
            with self.subTest(mutation=label):
                mutate()
                accounts = {account.identifier: account for account in load_accounts(self.accounts_file)}
                self.assertEqual(accounts["edge_a"].password.encode("utf-8"), expected)
                self.assertIn(record, self.accounts_file.read_bytes().split(b"\n"))

    def test_bulk_accounts_split_only_crlf_and_preserve_password_bytes(self):
        password = " kiri\u2028tengah\u2029\x85\v\f\x1c\x1d\x1eakhir "
        result = ui_backend.add_accounts_bulk(
            f"bulk_a|{password}\r\nbulk_b|sandi-dua\rbulk_c|sandi-tiga\n"
        )
        self.assertEqual(result, {"added": ["bulk_a", "bulk_b", "bulk_c"], "skipped": []})
        accounts = {account.identifier: account.password for account in load_accounts(self.accounts_file)}
        self.assertEqual(accounts, {"hapus_a": "test-only", "sisa_b": "test-only", "bulk_a": password,
                                    "bulk_b": "sandi-dua", "bulk_c": "sandi-tiga"})
        self.assertIn(f"bulk_a|{password}".encode("utf-8"), self.accounts_file.read_bytes().split(b"\n"))

    def test_duplicate_manifest_keys_are_not_overwritten(self):
        corrupt = '{"version":1,"jobs":[{"job_id":"first"}],"jobs":[]}'
        config.UI_JOBS_FILE.write_text(corrupt, encoding="utf-8")
        with self.assertRaises(ValueError):
            ui_backend.save_manifest({"version": 2, "contents": []})
        self.assertEqual(config.UI_JOBS_FILE.read_text(encoding="utf-8"), corrupt)

if __name__ == "__main__":
    unittest.main()
