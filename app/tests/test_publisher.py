# create by Ortseam
"""Bukti batas mutasi menggunakan Chromium + respons lokal, tanpa Instagram."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import ElementHandle, Error, Response, async_playwright
import config
import publisher
from models import Job, MediaItem
from publisher import publish, discard


class PublicationBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=config.chrome_path(), headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.job = Job("fixture-job", "fixture_account", "photo", (MediaItem(Path("fixture.jpg")),))
        self.original_timeout = config.PUBLISH_TIMEOUT_SECS
        config.PUBLISH_TIMEOUT_SECS = 0.2
        self.addCleanup(setattr, config, "PUBLISH_TIMEOUT_SECS", self.original_timeout)

    async def show(self, content):
        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body=content)
        await self.context.route("**/*", serve)
        await self.page.goto(config.INSTAGRAM_LOGIN_URL)

    async def test_intent_failure_prevents_share(self):
        await self.show('<div role="dialog"><button onclick="window.clicked=true">Share</button></div>')
        def unavailable_storage():
            raise OSError("storage unavailable")
        with self.assertRaises(OSError):
            await publish(self.page, self.job, unavailable_storage)
        self.assertFalse(await self.page.evaluate("Boolean(window.clicked)"))

    async def test_remote_success_after_one_share(self):
        await self.show('''<script>window.shares=0</script><div role="dialog"><button
            onclick="window.shares++;this.parentElement.innerHTML='<h1>Your post has been shared.</h1><a href=/p/fixture/>View post</a>'">Share</button></div>''')
        checkpoints = []
        result = await publish(self.page, self.job, lambda: checkpoints.append("durable"))
        self.assertEqual(result.status, "published")
        self.assertEqual(result.permalink, "https://www.instagram.com/p/fixture/")
        self.assertEqual(checkpoints, ["durable"])
        self.assertEqual(await self.page.evaluate("window.shares"), 1)

    async def test_uncertain_result_never_reclicks_share(self):
        await self.show('<script>window.shares=0</script><div role="dialog"><button onclick="window.shares++">Share</button></div>')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertEqual(await self.page.evaluate("window.shares"), 1)

    async def test_feed_confirmation_text_is_not_publish_proof(self):
        await self.show('<article>Your post has been shared.</article><div role="dialog"><button>Share</button></div>')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")

    async def configure(self, body: str, page: str, *, status: int = 200):
        # Route terakhir menang di Playwright: configure milik server, halaman lain memakai fixture.
        await self.show(page)
        async def serve(route):
            await route.fulfill(status=status, content_type="application/json", body=body)
        await self.context.route("https://www.instagram.com/api/v1/media/configure/", serve)

    async def test_configure_ok_is_publish_proof_and_permalink_source(self):
        await self.configure(
            '{"status":"ok","media":{"pk":"1","code":"FixturePost01"}}',
            '''<script>window.shares=0</script><div role="dialog"><button
            onclick="window.shares++;fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "published")
        self.assertEqual(result.permalink, "https://www.instagram.com/p/FixturePost01/")
        self.assertEqual(await self.page.evaluate("window.shares"), 1)

    async def test_configure_without_media_is_not_publish_proof(self):
        await self.configure(
            '{"status":"ok"}',
            '''<script>window.shares=0</script><div role="dialog"><button
            onclick="window.shares++;fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertIsNone(result.permalink)
        self.assertEqual(await self.page.evaluate("window.shares"), 1)

    async def test_configure_invalid_shortcode_is_not_publish_proof(self):
        await self.configure(
            '{"status":"ok","media":{"code":"../outside"}}',
            '''<div role="dialog"><button onclick="fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertIsNone(result.permalink)

    async def test_http_failure_with_media_is_not_publish_proof(self):
        await self.configure(
            '{"status":"ok","media":{"code":"FixturePost01"}}',
            '''<div role="dialog"><button onclick="fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''',
            status=500)
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertIsNone(result.permalink)

    async def publish_with_stalled_body(self, *, timeout=0.15):
        body_started = asyncio.Event()
        body_finished = asyncio.Event()

        async def stalled_json(response):
            body_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                body_finished.set()

        with patch.object(Response, "json", stalled_json), patch.object(config, "PUBLISH_TIMEOUT_SECS", timeout):
            result = await asyncio.wait_for(publish(self.page, self.job, lambda: None), timeout=1.5)
        self.assertTrue(body_started.is_set(), "Respons configure benar-benar dibaca.")
        self.assertTrue(body_finished.is_set(), "Pembacaan body harus selesai dibatalkan sebelum publish kembali.")
        self.assertEqual(await self.page.evaluate("window.shares"), 1)
        return result

    async def test_stalled_configure_body_keeps_deadline_and_one_share(self):
        await self.configure(
            '{"status":"ok","media":{"code":"FixturePost01"}}',
            '''<script>window.shares=0</script><div role="dialog"><button
            onclick="window.shares++;fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        result = await self.publish_with_stalled_body()
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertIsNone(result.permalink)

    async def test_shared_dialog_confirms_while_configure_body_stalls(self):
        await self.configure(
            '{"status":"ok","media":{"code":"FixturePost01"}}',
            '''<script>window.shares=0</script><div role="dialog"><button
            onclick="window.shares++;fetch('/api/v1/media/configure/',{method:'POST'}).then(() => {
                setTimeout(() => {this.parentElement.innerHTML='<h1>Your post has been shared.</h1><a href=/p/fixture/>View post</a>';}, 20);
            })">Share</button></div>''')
        result = await self.publish_with_stalled_body(timeout=0.5)
        self.assertEqual(result.status, "published")
        self.assertEqual(result.permalink, "https://www.instagram.com/p/fixture/")

    async def test_configure_failure_keeps_result_uncertain_with_server_message(self):
        await self.configure(
            '{"status":"fail","message":"Media configure failed"}',
            '''<div role="dialog"><button onclick="fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertIn("Media configure failed", result.detail)

    async def test_configure_get_or_foreign_origin_is_not_proof(self):
        await self.show('''<div role="dialog"><button onclick="fetch('/api/v1/media/configure/')">Share</button></div>''')
        async def serve(route):
            await route.fulfill(status=200, content_type="application/json", body='{"status":"ok","media":{"code":"ABCDEF"}}')
        await self.context.route("https://www.instagram.com/api/v1/media/configure/", serve)
        result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "unknown_after_submit")

    async def test_covered_share_action_respects_publication_deadline(self):
        await self.show('<div role="dialog"><button onclick="window.shares=(window.shares||0)+1">Share</button></div>')
        original_click = ElementHandle.click
        intents = []

        async def cover_before_click(handle, *args, **kwargs):
            await self.page.evaluate("""() => {
                const overlay = document.createElement('div');
                overlay.style = 'position:fixed;inset:0;z-index:99999;background:white';
                document.body.append(overlay);
            }""")
            return await original_click(handle, *args, **kwargs)

        with patch.object(ElementHandle, "click", cover_before_click):
            result = await asyncio.wait_for(publish(self.page, self.job, lambda: intents.append("durable")), timeout=1.5)
        self.assertEqual(result.status, "unknown_after_submit")
        self.assertEqual(intents, ["durable"])
        self.assertEqual(await self.page.evaluate("window.shares || 0"), 0)

    async def test_completed_configure_proof_survives_later_click_error(self):
        await self.configure(
            '{"status":"ok","media":{"code":"ProofFixture01"}}',
            '''<div role="dialog"><button onclick="window.shares=(window.shares||0)+1;fetch('/api/v1/media/configure/',{method:'POST'})">Share</button></div>''')
        proof_ready = asyncio.Event()
        original_verdict = publisher._configure_verdict
        original_click = ElementHandle.click

        async def observe_proof(*args):
            value = await original_verdict(*args)
            if value[0]:
                proof_ready.set()
            return value

        async def fail_after_proof(handle, *args, **kwargs):
            await original_click(handle, *args, **kwargs)
            await asyncio.wait_for(proof_ready.wait(), 2)
            await asyncio.sleep(0)
            raise Error("Gangguan navigasi fixture sesudah bukti diterima")

        with patch.object(config, "PUBLISH_TIMEOUT_SECS", 3), \
             patch.object(publisher, "_configure_verdict", observe_proof), \
             patch.object(ElementHandle, "click", fail_after_proof):
            result = await publish(self.page, self.job, lambda: None)
        self.assertEqual(result.status, "published")
        self.assertEqual(result.permalink, "https://www.instagram.com/p/ProofFixture01/")
        self.assertEqual(await self.page.evaluate("window.shares"), 1)


if __name__ == "__main__":
    unittest.main()
