# create by Ortseam
"""Regresi penyimpanan Konten: Chromium + API lokal dengan seluruh data terisolasi."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright
import config
import ui_backend
import ui_server


class ContentPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ig-content-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.accounts = self.root / "accounts.txt"
        self.accounts.write_text("uji_a|test-only\nuji_b|test-only\n", encoding="utf-8")
        self.manifest = self.root / "jobs.json"
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        for name in ("a.png", "b.png"):
            (self.media / name).write_bytes(png)
        self.import_file = self.root / "import.png"
        self.import_file.write_bytes(png)
        paths = patch.multiple(config,
            INPUT_ACCOUNTS_FILE=self.accounts,
            UI_JOBS_FILE=self.manifest,
            UI_MEDIA_DIR=self.media,
            UI_SETTINGS_FILE=self.root / "settings.json",
            UI_MEDIA_TRASH_DIR=self.root / "trash",
            UI_RUN_HISTORY_FILE=self.root / "history.json",
            RESULT_FILE=self.root / "results.json",
            LEDGER_FILE=self.root / "ledger.json",
            OUTPUT_DIR=self.root / "output",
            LOG_FILE=self.root / "log.txt",
        )
        paths.start()
        self.addCleanup(paths.stop)
        sessions = patch.dict("os.environ", {"IG_SESSION_DIR": str(self.root / "profiles")})
        sessions.start()
        self.addCleanup(sessions.stop)
        picker = patch.object(ui_backend, "pick_media_files", return_value=[str(self.import_file)])
        picker.start()
        self.addCleanup(picker.stop)
        self.server = ui_server.create_server(port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def close_server():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close_server)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=config.chrome_path(), headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page(viewport={"width": 1440, "height": 1000})
        no_child = patch.object(ui_backend.subprocess, "Popen", side_effect=AssertionError("UI tests must not launch an engine"))
        no_child.start()
        self.addCleanup(no_child.stop)

    def content(self, *, content_id="a" * 32, name="uji-konten", **payload):
        return {"content_id": content_id, "name": name, "kind": "photo",
                "media": [{"path": "media/a.png"}], **payload}

    async def open_contents(self, contents, *, bindings=None, expand=True):
        await self.open_manifest({"version": 2, "contents": contents, "bindings": bindings or {}}, expand=expand)

    async def open_manifest(self, document, *, expand=True):
        self.manifest.write_text(json.dumps(document), encoding="utf-8")
        await self.page.goto(self.origin + "/#/konten")
        await self.page.locator('.view[data-route="konten"]').wait_for(state="visible")
        # Kartu dari file tertutup; buka lewat kontrol yang sama dengan pengguna.
        if expand and (document.get("contents") or document.get("jobs")):
            await self.page.locator("#job-list .job.collapsed").first.wait_for()
            if await self.page.locator("#fold-toggle").is_visible():
                await self.page.locator("#fold-toggle").click()
            else:
                while await self.page.locator("#job-list .job.collapsed").count():
                    await self.page.locator("#job-list .job.collapsed .job-toggle").first.click()
            await self.page.locator("#job-list .job.collapsed").first.wait_for(state="detached")

    async def test_loaded_cards_start_collapsed_until_opened(self):
        contents = [self.content(content_id=f"{index + 1:032x}", name=f"konten-{index}", caption=f"Caption {index}")
                    for index in range(3)]
        await self.open_contents(contents, expand=False)
        cards = self.page.locator("#job-list .job")
        self.assertEqual(await cards.count(), 3)
        self.assertEqual(await self.page.locator("#job-list .job.collapsed").count(), 3)
        self.assertEqual(await self.page.locator("#job-list .job .f-caption:visible").count(), 0)
        await cards.nth(1).locator(".job-head").click()
        self.assertEqual(await self.page.locator("#job-list .job.collapsed").count(), 2)
        self.assertEqual(await cards.nth(1).locator(".f-caption").input_value(), "Caption 1")
        await self.page.locator("#new-job").click()
        self.assertFalse(await self.page.locator("#job-list .job").last.evaluate("node => node.classList.contains('collapsed')"))
        await self.page.reload()
        await self.page.locator("#job-list .job").first.wait_for()
        self.assertEqual(await self.page.locator("#job-list .job.collapsed").count(), 3)

    def saved(self):
        return json.loads(self.manifest.read_text(encoding="utf-8"))["contents"]

    async def save(self):
        async with self.page.expect_response(lambda response: response.url.endswith("/api/jobs")) as response:
            await self.page.locator("#save-jobs").click()
        self.assertEqual((await response.value).status, 200)
        await self.page.wait_for_function("() => !document.querySelector('#save-jobs').disabled")
        await self.page.locator("#jobs-dirty").wait_for(state="hidden")

    async def test_corrupt_manifest_cannot_be_overwritten_by_editor_or_api(self):
        corrupt = b'{"version":1,"jobs":['
        self.manifest.write_bytes(corrupt)
        await self.page.goto(self.origin + "/#/konten")
        await self.page.locator("#manifest-error").wait_for(state="visible")
        self.assertTrue(await self.page.locator("#save-jobs").is_disabled())
        self.assertTrue(await self.page.locator("#new-job").is_disabled())
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        self.assertTrue(await self.page.locator("#start-run").is_disabled())
        token = (await (await self.page.request.get(self.origin + "/api/bootstrap")).json())["token"]
        response = await self.page.request.post(self.origin + "/api/jobs",
            data={"manifest": {"version": 2, "contents": []}}, headers={"X-UI-Token": token})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.manifest.read_bytes(), corrupt)
        self.manifest.write_text('{"version":2,"contents":[],"bindings":{}}', encoding="utf-8")
        await self.page.reload()
        await self.page.locator('.rail-item[data-route="konten"]').click()
        await self.page.wait_for_function("() => !document.querySelector('#new-job').disabled")

    async def test_old_server_payload_cannot_turn_legacy_catalog_into_empty_save(self):
        legacy = {"version": 1, "jobs": [{"job_id": "kept", "account": "uji_a", "kind": "photo",
                                           "media": [{"path": "media/a.png"}]}]}

        async def old_server(route):
            response = await route.fetch()
            payload = await response.json()
            payload["manifest"] = legacy
            payload["manifest_error"] = None
            await route.fulfill(response=response, json=payload)

        await self.page.route("**/api/bootstrap", old_server)
        await self.open_manifest(legacy, expand=False)
        original = self.manifest.read_bytes()
        mutations = []
        self.page.on("request", lambda request: mutations.append(request.url)
                     if request.method == "POST" and request.url.endswith(("/api/jobs", "/api/run/start")) else None)
        await self.page.locator("#manifest-error").wait_for(state="visible")
        self.assertTrue(await self.page.locator("#new-job").is_disabled())
        self.assertTrue(await self.page.locator("#save-jobs").is_disabled())
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        self.assertTrue(await self.page.locator("#start-run").is_disabled())
        self.assertEqual(mutations, [])
        self.assertEqual(self.manifest.read_bytes(), original)
        await self.page.unroute("**/api/bootstrap", old_server)
        await self.page.reload()
        await self.page.locator('.rail-item[data-route="konten"]').click()
        await self.page.wait_for_function("() => !document.querySelector('#new-job').disabled")
        self.assertEqual(await self.page.locator("#job-list .job").count(), 1)
        self.assertEqual(self.manifest.read_bytes(), original)

    async def test_unsaved_carousel_media_cannot_be_archived(self):
        await self.open_contents([self.content(kind="carousel",
                                               media=[{"path": "media/a.png"}, {"path": "media/b.png"}])])
        await self.page.get_by_role("button", name="Media 2: b.png").click()
        await self.page.get_by_role("button", name="Ganti", exact=True).click()
        await self.page.get_by_role("button", name="Impor file baru", exact=True).click()
        await self.page.locator(".picker-tile.selected").wait_for()
        await self.page.get_by_role("button", name="Pakai", exact=True).click()
        self.assertFalse(await self.page.locator("#jobs-dirty").evaluate("node => node.classList.contains('hidden')"))
        await self.page.locator('.rail-item[data-route="media"]').click()
        card = self.page.locator("#media-grid .media-card").filter(has=self.page.locator(".media-name", has_text="import.png"))
        self.assertEqual(await card.count(), 1)
        requests = []
        self.page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/api/media/delete") else None)
        await card.get_by_role("button", name="Pindahkan ke arsip").click()
        self.assertEqual(requests, [])
        self.assertTrue((self.media / "import.png").is_file())
        self.assertEqual(self.saved()[0]["media"][1]["path"], "media/b.png")

    async def test_unsaved_cover_media_cannot_be_archived(self):
        await self.open_contents([self.content()])
        await self.page.locator(".f-kind").select_option("reel")
        await self.page.locator(".job-form details.expander summary").click()
        await self.page.locator(".f-cover").select_option("media/b.png")
        await self.page.locator('.rail-item[data-route="media"]').click()
        card = self.page.locator("#media-grid .media-card").filter(has=self.page.locator(".media-name", has_text="b.png"))
        requests = []
        self.page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/api/media/delete") else None)
        await card.get_by_role("button", name="Pindahkan ke arsip").click()
        self.assertEqual(requests, [])
        self.assertTrue((self.media / "b.png").is_file())
        self.assertEqual(self.saved()[0]["media"][0]["path"], "media/a.png")

    async def test_distinct_nested_media_never_merge_during_save(self):
        jobs = [
            {"job_id": "promo-uji_a", "account": "uji_a", "kind": "carousel", "caption": "Sama",
             "media": [{"path": "media/a.png", "alt_text": "Pertama"}, {"path": "media/b.png"}]},
            {"job_id": "promo-uji_b", "account": "uji_b", "kind": "carousel", "caption": "Sama",
             "media": [{"path": "media/b.png", "alt_text": "Berbeda"}, {"path": "media/a.png"}]},
        ]
        await self.open_manifest({"version": 1, "jobs": jobs})
        self.assertEqual(await self.page.locator("#job-list .job").count(), 2)
        await self.save()
        self.assertEqual([content["media"] for content in self.saved()], [job["media"] for job in jobs])
        self.assertEqual([content["caption"] for content in self.saved()], ["Sama", "Sama"])

    async def test_content_can_be_authored_saved_and_duplicated_without_accounts(self):
        self.accounts.write_text("", encoding="utf-8")
        await self.open_contents([])
        await self.page.locator("#new-job").click()
        await self.page.locator(".f-job-id").fill("tanpa-akun")
        await self.page.locator(".f-caption").fill("Bisa disiapkan sebelum akun ditambahkan")
        await self.save()
        original = self.saved()[0]
        self.assertEqual(original["name"], "tanpa-akun")
        self.assertRegex(original["content_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("account", original)
        self.assertNotIn("accounts", original)
        self.assertNotIn("job_id", original)
        self.assertEqual(await self.page.locator("#job-list .account-picker").count(), 0)
        await self.page.locator(".job-duplicate").click()
        await self.save()
        self.assertEqual(len(self.saved()), 2)
        self.assertNotEqual(self.saved()[0]["content_id"], self.saved()[1]["content_id"])
        self.assertEqual(self.saved()[1]["media"], original["media"])
        self.assertEqual(self.saved()[1]["caption"], original["caption"])
        await self.page.reload()
        await self.page.locator("#job-list .job").first.wait_for()
        self.assertEqual(await self.page.locator("#job-list .job").count(), 2)
        self.assertEqual(self.accounts.read_text(encoding="utf-8"), "")

    async def test_import_from_clean_card_saves_selected_file(self):
        await self.open_contents([self.content()])
        await self.page.get_by_role("button", name="Ganti", exact=True).click()
        await self.page.get_by_role("button", name="Impor file baru", exact=True).click()
        await self.page.locator(".picker-tile.selected").wait_for()
        await self.page.get_by_role("button", name="Pakai", exact=True).click()
        await self.save()
        self.assertEqual(self.saved()[0]["media"][0]["path"], "media/import.png")
        await self.page.reload()
        self.assertEqual(await self.page.locator(".job-preview img").get_attribute("src"), "/api/media/file/import.png")

    async def test_edit_while_saving_remains_unsaved_and_visible(self):
        await self.open_contents([self.content(caption="Awal")])
        sent = asyncio.Event()
        release = asyncio.Event()

        async def hold_request(route):
            sent.set()
            await release.wait()
            await route.continue_()

        await self.page.route("**/api/jobs", hold_request)
        try:
            await self.page.locator(".f-caption").fill("Versi dikirim")
            await self.page.locator("#save-jobs").click()
            await asyncio.wait_for(sent.wait(), 5)
            await self.page.locator(".f-caption").fill("Edit baru selama simpan")
            release.set()
            await self.page.wait_for_function("() => !document.querySelector('#save-jobs').disabled")
            self.assertEqual(await self.page.locator(".f-caption").input_value(), "Edit baru selama simpan")
            self.assertFalse(await self.page.locator("#jobs-dirty").evaluate("node => node.classList.contains('hidden')"))
            self.assertEqual(self.saved()[0]["caption"], "Versi dikirim")
        finally:
            release.set()
            await self.page.unroute("**/api/jobs", hold_request)
        await self.save()
        self.assertEqual(self.saved()[0]["caption"], "Edit baru selama simpan")

    async def test_account_save_preserves_next_account_entered_while_waiting(self):
        await self.open_contents([], expand=False)
        await self.page.locator('.rail-item[data-route="akun"]').click()
        sent, release = asyncio.Event(), asyncio.Event()

        async def hold_response(route):
            response = await route.fetch()
            sent.set()
            await release.wait()
            await route.fulfill(response=response)

        await self.page.route("**/api/accounts", hold_response)
        try:
            await self.page.locator("#account-identifier").fill("first_new")
            await self.page.locator("#account-password").fill("first-fixture")
            await self.page.locator('#account-form button[type="submit"]').click()
            await asyncio.wait_for(sent.wait(), 5)
            await self.page.locator("#account-identifier").fill("next_new")
            await self.page.locator("#account-password").fill("next-fixture")
            async with self.page.expect_response(lambda response: response.url.endswith("/api/state")):
                release.set()
            self.assertEqual(await self.page.locator("#account-identifier").input_value(), "next_new")
            self.assertEqual(await self.page.locator("#account-password").input_value(), "next-fixture")
            self.assertEqual([a.identifier for a in ui_backend.list_accounts()[0]], ["uji_a", "uji_b", "first_new"])
        finally:
            release.set()
            await self.page.unroute("**/api/accounts", hold_response)
        async with self.page.expect_response(lambda response: response.url.endswith("/api/state")):
            await self.page.locator('#account-form button[type="submit"]').click()
        self.assertEqual([a.identifier for a in ui_backend.list_accounts()[0]], ["uji_a", "uji_b", "first_new", "next_new"])
        self.assertEqual(await self.page.locator("#account-password").input_value(), "")

    async def test_bulk_account_save_preserves_next_batch_entered_while_waiting(self):
        await self.open_contents([], expand=False)
        await self.page.locator('.rail-item[data-route="akun"]').click()
        await self.page.locator('.view[data-route="akun"] details summary').click()
        sent, release = asyncio.Event(), asyncio.Event()

        async def hold_response(route):
            response = await route.fetch()
            sent.set()
            await release.wait()
            await route.fulfill(response=response)

        await self.page.route("**/api/accounts/bulk", hold_response)
        try:
            await self.page.locator("#bulk-accounts-text").fill("first_batch|first-fixture")
            await self.page.locator("#bulk-accounts-submit").click()
            await asyncio.wait_for(sent.wait(), 5)
            await self.page.locator("#bulk-accounts-text").fill("next_batch|next-fixture")
            async with self.page.expect_response(lambda response: response.url.endswith("/api/state")):
                release.set()
            self.assertEqual(await self.page.locator("#bulk-accounts-text").input_value(), "next_batch|next-fixture")
            self.assertEqual([a.identifier for a in ui_backend.list_accounts()[0]], ["uji_a", "uji_b", "first_batch"])
        finally:
            release.set()
            await self.page.unroute("**/api/accounts/bulk", hold_response)
        async with self.page.expect_response(lambda response: response.url.endswith("/api/state")):
            await self.page.locator("#bulk-accounts-submit").click()
        self.assertEqual([a.identifier for a in ui_backend.list_accounts()[0]], ["uji_a", "uji_b", "first_batch", "next_batch"])

    async def test_retired_saved_mode_migrates_without_enabling_publication(self):
        old = {"mode": "prepare", "workers": 3, "manual_timeout": 17, "headless": True}
        settings_file = self.root / "settings.json"
        settings_file.write_text(json.dumps(old), encoding="utf-8")
        await self.open_contents([])
        expected = {**old, "mode": "login"}
        self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8")), expected)
        await self.page.goto(self.origin + "/#/jalankan")
        await self.page.locator('.mode-card input[value="login"]:checked').wait_for(state="attached")
        self.assertEqual(await self.page.locator('input[name="mode"]').evaluate_all("nodes => nodes.map(node => node.value)"), ["login", "publish"])
        token = (await (await self.page.request.get(self.origin + "/api/bootstrap")).json())["token"]
        for path in ("/api/settings", "/api/run/start"):
            response = await self.page.request.post(self.origin + path,
                data={"settings": old, "confirm": "PUBLISH"}, headers={"X-UI-Token": token})
            self.assertEqual(response.status, 400)
        response = await self.page.request.post(self.origin + "/api/run/start",
            data={"settings": {**expected, "mode": "publish"}}, headers={"X-UI-Token": token})
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8")), expected)

    async def open_run(self, *, publish=True):
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        if publish:
            await self.page.locator('.mode-card input[value="publish"] + span').click()
        await self.page.locator("#run-accounts-list input").first.wait_for(state="attached")

    async def capture_starts(self):
        bodies = []

        async def capture(route):
            bodies.append(json.loads(route.request.post_data))
            await route.fulfill(status=400, content_type="application/json",
                                body='{"error":"Fixture tidak menjalankan engine."}')

        await self.page.route("**/api/run/start", capture)
        return bodies

    async def confirm_start(self):
        await self.page.locator("#dialog-input").fill("PUBLISH")
        async with self.page.expect_response(lambda response: response.url.endswith("/api/run/start")):
            await self.page.locator("#dialog-confirm").click()
        await self.page.wait_for_function("() => !document.querySelector('#start-run').disabled")

    async def selected(self, container):
        return await self.page.locator(f"{container} input:checked").evaluate_all("nodes => nodes.map(node => node.value).sort()")

    async def test_explicit_multi_selection_survives_autosave_and_account_refresh(self):
        self.accounts.write_text("uji_a|test-only\nuji_b|test-only\nuji_c|test-only\n", encoding="utf-8")
        contents = [self.content(content_id=f"{index + 1:032x}", name=f"konten-{index}") for index in range(3)]
        await self.open_contents(contents, expand=False)
        await self.open_run()
        self.assertEqual(await self.selected("#run-content-list"), [])
        self.assertEqual(await self.selected("#run-accounts-list"), [])
        bodies = await self.capture_starts()
        await self.page.locator("#run-content-all").click()
        await self.page.locator(f'input[data-content-id="{contents[2]["content_id"]}"]').uncheck()
        await self.page.locator("#run-accounts-all").click()
        await self.page.locator('input[data-account="uji_c"]').uncheck()
        wanted_contents = [item["content_id"] for item in contents[:2]]
        self.assertEqual(await self.selected("#run-content-list"), wanted_contents)
        self.assertEqual(await self.selected("#run-accounts-list"), ["uji_a", "uji_b"])

        await self.page.locator('.rail-item[data-route="konten"]').click()
        await self.page.locator(".job-toggle").first.click()
        await self.page.locator(".f-job-id").first.fill("nama-diubah")
        await self.page.locator(".f-caption").first.fill("Edit harus tersimpan sebelum konfirmasi")
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        async with self.page.expect_response(lambda response: response.url.endswith("/api/jobs")) as saved:
            await self.page.locator("#start-run").click()
        self.assertEqual((await saved.value).status, 200)
        await self.page.locator("#dialog-input").wait_for()
        confirmation = await self.page.locator("#dialog-text").inner_text()
        for selected_name in ("nama-diubah", "konten-1", "uji_a", "uji_b"):
            self.assertIn(selected_name, confirmation)
        await self.page.locator("#dialog-cancel").click()
        self.assertEqual(bodies, [])
        self.assertEqual(self.saved()[0]["content_id"], wanted_contents[0])
        self.assertEqual(self.saved()[0]["caption"], "Edit harus tersimpan sebelum konfirmasi")
        self.assertEqual(await self.selected("#run-content-list"), wanted_contents)
        self.assertEqual(await self.selected("#run-accounts-list"), ["uji_a", "uji_b"])

        await self.page.locator('.rail-item[data-route="akun"]').click()
        await self.page.locator("#account-identifier").fill("uji_d")
        await self.page.locator("#account-password").fill("test-only")
        async with self.page.expect_response(lambda response: response.url.endswith("/api/state")):
            await self.page.locator('#account-form button[type="submit"]').click()
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        await self.page.locator('input[data-account="uji_d"]').wait_for(state="attached")
        self.assertEqual(await self.selected("#run-content-list"), wanted_contents)
        self.assertEqual(await self.selected("#run-accounts-list"), ["uji_a", "uji_b"])
        await self.page.locator("#start-run").click()
        await self.confirm_start()
        self.assertEqual(len(bodies), 1)
        self.assertEqual(set(bodies[0]["contents"]), set(wanted_contents))
        self.assertEqual(set(bodies[0]["accounts"]), {"uji_a", "uji_b"})
        self.assertEqual(bodies[0]["confirm"], "PUBLISH")
        self.assertFalse(bodies[0]["repost"])
        self.assertNotIn("jobs", bodies[0])
        self.assertNotIn("account", bodies[0])

    async def test_login_uses_only_checked_accounts_without_content_selection(self):
        await self.open_contents([self.content()], expand=False)
        await self.open_run(publish=False)
        bodies = await self.capture_starts()
        await self.page.locator('input[data-account="uji_b"]').check()
        self.assertFalse(await self.page.locator("#run-content").is_visible())
        async with self.page.expect_response(lambda response: response.url.endswith("/api/run/start")):
            await self.page.locator("#start-run").click()
        self.assertEqual(bodies[0]["accounts"], ["uji_b"])
        self.assertFalse(bodies[0].get("contents"))
        self.assertFalse(bodies[0].get("repost"))

    def write_ledger(self, entries):
        rows = {job_id: {"digest": "a" * 64, "account": account, "status": status, "detail": "",
                         "permalink": None, "updated_at": "2026-01-01T00:00:00+00:00"}
                for job_id, account, status in entries}
        (self.root / "ledger.json").write_text(json.dumps({"version": 1, "jobs": rows}), encoding="utf-8")

    async def test_partial_ledger_repost_is_explicit_without_copying_content(self):
        content = self.content(name="kampanye")
        self.write_ledger([("lama-a", "uji_a", "published")])
        await self.open_contents([content], bindings={content["content_id"]: {"uji_a": ["lama-a"]}}, expand=False)
        original_catalog = self.manifest.read_bytes()
        original_ledger = (self.root / "ledger.json").read_bytes()
        await self.open_run()
        bodies = await self.capture_starts()
        await self.page.locator("#run-content-all").click()
        await self.page.locator("#run-accounts-all").click()
        badge = self.page.locator("#run-content-list .chip span:last-child")
        await self.page.wait_for_function("() => document.querySelector('#run-content-list .chip span:last-child').textContent === 'Sebagian terposting'")
        self.assertFalse(await self.page.locator("#run-repost").is_checked())
        await self.page.locator("#start-run").click()
        await self.confirm_start()
        self.assertFalse(bodies[-1]["repost"])
        self.assertEqual(bodies[-1]["contents"], [content["content_id"]])
        self.assertEqual(set(bodies[-1]["accounts"]), {"uji_a", "uji_b"})

        await self.page.locator('input[data-account="uji_b"]').uncheck()
        self.assertEqual(await badge.inner_text(), "Sudah terposting")
        await self.page.locator("#run-repost-field").click()
        await self.page.locator("#start-run").click()
        await self.page.locator("#dialog-input").wait_for()
        await self.page.locator("#dialog-cancel").click()
        self.assertEqual(len(bodies), 1)
        self.assertEqual(self.manifest.read_bytes(), original_catalog)
        self.assertEqual((self.root / "ledger.json").read_bytes(), original_ledger)
        await self.page.locator("#start-run").click()
        await self.confirm_start()
        self.assertEqual(bodies[-1]["accounts"], ["uji_a"])
        self.assertTrue(bodies[-1]["repost"])
        self.assertEqual(bodies[-1]["contents"], [content["content_id"]])
        self.assertEqual(self.saved(), [content])
        self.assertEqual((self.root / "ledger.json").read_bytes(), original_ledger)

    async def test_uncertain_selected_pair_blocks_after_poll_without_clearing_choices(self):
        content = self.content()
        self.write_ledger([("attempt-a", "uji_a", "published")])
        await self.open_contents([content], bindings={content["content_id"]: {
            "uji_a": ["attempt-a"], "uji_b": ["attempt-b"],
        }}, expand=False)
        await self.open_run()
        bodies = await self.capture_starts()
        await self.page.locator("#run-content-all").click()
        await self.page.locator("#run-accounts-all").click()
        await self.page.locator("#run-repost-field").click()
        self.write_ledger([("attempt-a", "uji_a", "published"), ("attempt-b", "uji_b", "unknown_after_submit")])
        await self.page.wait_for_function("() => document.querySelector('#run-content-list .chip span:last-child').textContent === 'Perlu diperiksa'")
        self.assertEqual(await self.selected("#run-content-list"), [content["content_id"]])
        self.assertEqual(await self.selected("#run-accounts-list"), ["uji_a", "uji_b"])
        self.assertTrue(await self.page.locator("#start-run").is_disabled())
        self.assertEqual(bodies, [])
        await self.page.locator('input[data-account="uji_b"]').uncheck()
        self.assertEqual(await self.page.locator("#run-content-list .chip span:last-child").inner_text(), "Sudah terposting")
        self.assertFalse(await self.page.locator("#start-run").is_disabled())

    async def test_retry_only_selects_scope_and_still_requires_typed_confirmation(self):
        content = self.content()
        (self.root / "results.json").write_text(json.dumps([{
            "account": "uji_a", "job_id": "uji-konten", "mode": "publish", "status": "browser_error",
        }]), encoding="utf-8")

        await self.open_contents([content], bindings={content["content_id"]: {"uji_a": ["uji-konten"]}}, expand=False)
        await self.open_run()
        bodies = await self.capture_starts()
        await self.page.locator("#results-table tbody tr button").click()
        self.assertEqual(bodies, [])
        self.assertFalse(await self.page.locator("#dialog").is_visible())
        self.assertEqual(await self.selected("#run-accounts-list"), ["uji_a"])
        self.assertEqual(await self.selected("#run-content-list"), [content["content_id"]])
        await self.page.locator("#start-run").click()
        await self.page.locator("#dialog-input").fill("SALAH")
        await self.page.locator("#dialog-confirm").click()
        await self.page.locator("#dialog").wait_for(state="hidden")
        self.assertEqual(bodies, [])
        await self.page.locator("#start-run").click()
        await self.page.locator("#dialog-input").wait_for()
        await self.page.locator("#dialog-cancel").click()
        self.assertEqual(bodies, [])
        token = (await (await self.page.request.get(self.origin + "/api/bootstrap")).json())["token"]
        response = await self.page.request.get(self.origin + "/api/run", headers={"X-UI-Token": token})
        self.assertFalse((await response.json())["running"])

    async def test_invalid_saved_settings_keep_ui_available_and_require_repair(self):
        broken = '{"mode":"publish","workers":11}'
        config.UI_SETTINGS_FILE.write_text(broken, encoding="utf-8")
        await self.open_contents([self.content()], expand=False)
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        await self.page.locator("#settings-file-error").wait_for(state="visible")
        self.assertTrue(await self.page.locator("#start-run").is_disabled())
        self.assertEqual(config.UI_SETTINGS_FILE.read_text(encoding="utf-8"), broken)
        await self.page.locator("#advanced-settings summary").click()
        async with self.page.expect_response(lambda response: response.url.endswith("/api/settings")) as saving:
            await self.page.locator("#setting-workers").fill("3")
        self.assertEqual((await saving.value).status, 200)
        await self.page.locator("#settings-file-error").wait_for(state="hidden")
        self.assertFalse(await self.page.locator("#start-run").is_disabled())
        settings = json.loads(config.UI_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual((settings["mode"], settings["workers"]), ("login", 3))

    async def test_nontext_mode_returns_validation_json_without_disconnect(self):
        boot = await self.page.request.get(self.origin + "/api/bootstrap")
        token = (await boot.json())["token"]
        response = await self.page.request.post(self.origin + "/api/settings",
            headers={"X-UI-Token": token}, data={"settings": {"mode": []}})
        self.assertEqual(response.status, 400)
        self.assertIn("error", await response.json())
        self.assertFalse(config.UI_SETTINGS_FILE.exists())

    async def test_populated_history_stays_within_compact_viewport(self):
        config.UI_RUN_HISTORY_FILE.write_text(json.dumps([{
            "started_at": "2026-01-01T00:00:00+00:00", "settings": {"mode": "publish"},
            "accounts": ["uji_a", "uji_b"], "exit_code": 0,
            "results": [{"status": "published"}], "log_tail": "fixture",
        }]), encoding="utf-8")
        await self.open_contents([], expand=False)
        await self.page.set_viewport_size({"width": 360, "height": 800})
        await self.page.locator('.rail-item[data-route="riwayat"]').click()
        await self.page.locator('.view[data-route="riwayat"]').wait_for(state="visible")
        await self.page.evaluate("() => document.getAnimations().forEach(animation => animation.finish())")
        self.assertLessEqual(await self.page.evaluate("() => document.documentElement.scrollWidth"), 361)
        region = self.page.locator('#history-table').locator('..')
        await region.evaluate("node => { node.scrollLeft = node.scrollWidth; }")
        self.assertGreater(await region.evaluate("node => node.scrollLeft"), 0)
        await self.page.get_by_role("button", name="Lihat log", exact=True).click()
        self.assertEqual(await self.page.locator("#history-table .log").inner_text(), "fixture")

    async def test_picker_applies_to_current_content_after_background_refresh(self):
        await self.open_contents([self.content()])
        await self.page.locator(".job-media-actions").get_by_role("button", name="Ganti", exact=True).click()
        await self.page.evaluate("() => refresh()")
        await self.page.locator('.picker-tile[title="b.png"]').click()
        await self.page.locator("#dialog-confirm").click()
        await self.save()
        self.assertEqual(self.saved()[0]["media"][0]["path"], "media/b.png")

    async def test_delete_confirmation_uses_identity_after_catalog_reorder(self):
        first, second = self.content(), self.content(content_id="b" * 32, name="kedua")
        await self.open_contents([first, second])
        await self.page.locator(".job-remove").first.click()
        ui_backend.save_manifest({"version": 2, "contents": [second, first]})
        await self.page.evaluate("() => refresh()")
        await self.page.locator("#dialog-confirm").click()
        await self.save()
        self.assertEqual([item["content_id"] for item in self.saved()], [second["content_id"]])

    async def test_published_with_failed_cleanup_warns_without_offering_retry(self):
        config.RESULT_FILE.write_text(json.dumps([{
            "account": "uji_a", "mode": "publish", "status": "published", "cleanup": "failed",
        }]), encoding="utf-8")
        await self.open_contents([], expand=False)
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        await self.page.locator("#results-table .chip.warn").wait_for()
        self.assertIn("Posting terkonfirmasi", await self.page.locator("#results-table tbody").inner_text())
        self.assertEqual(await self.page.locator("#results-table tbody button").count(), 0)

    async def test_older_snapshot_cannot_roll_back_newer_saved_caption(self):
        await self.open_contents([self.content(caption="old")])
        await self.page.evaluate("""async () => {
            const fetchNow = window.fetch;
            const old = await fetchNow('/api/state', {headers: {'X-UI-Token': state.token}});
            window.fetch = (url, options) => url === '/api/state'
                ? new Promise(resolve => { window.releaseOld = () => resolve(old); }) : fetchNow(url, options);
            window.pendingRefresh = refresh();
            window.fetch = fetchNow;
        }""")
        await self.page.locator(".f-caption").fill("newest")
        await self.save()
        await self.page.evaluate("async () => { window.releaseOld(); await window.pendingRefresh; }")
        self.assertEqual(await self.page.locator(".f-caption").input_value(), "newest")
        await self.save()
        self.assertEqual(self.saved()[0]["caption"], "newest")

    async def test_latest_mode_wins_when_previous_save_is_pending(self):
        await self.open_contents([], expand=False)
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        await self.page.evaluate("""() => {
            window.originalFetch = window.fetch;
            window.fetch = (url, options) => url === '/api/settings'
                ? new Promise(resolve => { window.releaseSettings = () => resolve(window.originalFetch(url, options)); })
                : window.originalFetch(url, options);
        }""")
        await self.page.locator('.mode-card.danger').click()
        await self.page.evaluate("() => refresh()")
        self.assertTrue(await self.page.locator('input[name="mode"][value="publish"]').is_checked())
        await self.page.locator('.mode-card:not(.danger)').click()
        await self.page.evaluate("async () => { window.fetch = window.originalFetch; window.releaseSettings(); await settingsQueue; }")
        await self.page.reload()
        await self.page.locator("#run-accounts-list input").first.wait_for()
        self.assertTrue(await self.page.locator('input[name="mode"][value="login"]').is_checked())
        self.assertEqual(json.loads(config.UI_SETTINGS_FILE.read_text())["mode"], "login")

    async def test_enter_confirmation_dispatches_only_reviewed_content(self):
        content = self.content(caption="reviewed")
        await self.open_contents([content], expand=False)
        await self.open_run()
        bodies = await self.capture_starts()
        await self.page.locator("#run-content-all").click()
        await self.page.locator('input[data-account="uji_a"]').check()
        await self.page.locator("#start-run").click()
        await self.page.locator("#dialog-input").fill("PUBLISH")
        async with self.page.expect_response(lambda response: response.url.endswith("/api/run/start")):
            await self.page.locator("#dialog-input").press("Enter")
        await self.page.wait_for_function("() => !document.querySelector('#dialog').open")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["expected_contents"], [content])

    async def test_unicode_caption_and_labeled_ranges_remain_editable(self):
        await self.open_contents([self.content()])
        caption = self.page.locator(".f-caption")
        await caption.fill("\U0001f600" * 1100)
        await caption.press("End")
        await self.page.keyboard.type("a")
        self.assertEqual(await caption.input_value(), "\U0001f600" * 1100 + "a")
        await self.page.locator(".job-form > details > summary").click()
        zoom = self.page.get_by_role("slider", name="Zoom (0..100)", exact=True)
        await zoom.focus()
        await zoom.press("ArrowRight")
        await self.save()
        self.assertEqual(self.saved()[0]["media"][0]["zoom"], 1)

    async def test_poll_retains_keyboard_focus_on_unchanged_result_action(self):
        config.RESULT_FILE.write_text(json.dumps([{"account": "uji_a", "mode": "login", "status": "browser_error"}]), encoding="utf-8")
        await self.open_contents([], expand=False)
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        action = self.page.locator("#results-table tbody button")
        await action.wait_for(state="visible")
        await action.focus()
        await self.page.evaluate("() => pollRun()")
        self.assertTrue(await action.evaluate("node => document.activeElement === node"))
        await self.page.keyboard.press("Enter")
        self.assertTrue(await self.page.locator('input[data-account="uji_a"]').is_checked())

    async def test_next_dialog_survives_previous_close_event(self):
        await self.open_contents([], expand=False)
        await self.page.evaluate("""() => {
            window.dialogAnswers = [];
            dialog({title: 'Pertama', text: 'fixture'}).then(answer => {
                window.dialogAnswers.push(answer);
                return dialog({title: 'Kedua', text: 'fixture'});
            }).then(answer => window.dialogAnswers.push(answer));
        }""")
        await self.page.locator("#dialog-confirm").click()
        await self.page.get_by_role("heading", name="Kedua", exact=True).wait_for()
        self.assertTrue(await self.page.locator("#dialog").is_visible())
        self.assertEqual(await self.page.evaluate("window.dialogAnswers"), [True])
        await self.page.locator("#dialog-cancel").click()
        await self.page.wait_for_function("() => window.dialogAnswers.length === 2")
        self.assertEqual(await self.page.evaluate("window.dialogAnswers"), [True, False])

    async def test_dismissed_picker_completion_cannot_disable_new_dialog(self):
        await self.open_contents([], expand=False)
        await self.page.evaluate("""() => {
            const original = window.fetch;
            window.fetch = (url, options) => {
                if (url === '/api/media/pick') return new Promise(resolve => {
                    window.finishPicker = () => resolve({ok:true, json:async () => ({ok:true, imported:[]})});
                });
                if (url === '/api/state') return Promise.resolve({ok:true, json:async () => ({media:state.media})});
                return original(url, options);
            };
            window.pickerResult = pickMediaDialog({kind:'photo'});
        }""")
        await self.page.locator("#dialog-slot button").last.click()
        await self.page.locator("#dialog-cancel").click()
        await self.page.evaluate("""async () => {
            window.newDialog = dialog({title:'Konfirmasi baru',text:'fixture'});
            window.finishPicker();
            await new Promise(resolve => setTimeout(resolve, 0));
        }""")
        self.assertTrue(await self.page.locator("#dialog-confirm").is_enabled())
        await self.page.locator("#dialog-confirm").click()
        self.assertTrue(await self.page.evaluate("() => window.newDialog"))

    async def test_stop_ui_waits_for_backend_completion(self):
        await self.open_contents([], expand=False)
        token = (await (await self.page.request.get(self.origin + "/api/bootstrap")).json())["token"]
        response = await self.page.request.get(self.origin + "/api/run", headers={"X-UI-Token": token})
        run = await response.json()
        run.update(running=True, stopping=False, settings={"mode":"login","workers":1,"headless":False}, accounts=["uji_a"])

        async def status_route(route):
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(run))

        async def stop_route(route):
            run["stopping"] = True
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(run))

        await self.page.route("**/api/run", status_route)
        await self.page.route("**/api/run/stop", stop_route)
        await self.page.evaluate("() => pollRun()")
        await self.page.locator('.rail-item[data-route="jalankan"]').click()
        await self.page.locator("#stop-run").click()
        await self.page.wait_for_function("() => document.querySelector('#stop-run').disabled")
        self.assertTrue(await self.page.locator("#start-run").is_disabled())
        self.assertTrue(await self.page.locator("#rail-run").is_disabled())
        self.assertIn("Sedang menghentikan", await self.page.locator("#run-badge").inner_text())
        run.update(running=False, stopping=False, exit_code=130)
        await self.page.evaluate("() => pollRun()")
        self.assertFalse(await self.page.locator("#start-run").is_disabled())
        self.assertEqual(await self.page.locator("#run-badge .status-chip-label").inner_text(), "Dihentikan")


if __name__ == "__main__":
    unittest.main()
