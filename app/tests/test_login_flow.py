# create by Ortseam
"""Regresi perilaku login dengan Chrome dan seluruh request diintersep lokal."""

import asyncio
import json
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from accounts import Account
import config
from login_flow import login


def navigation(username="fixture_user", *, avatar_username=None):
    avatar_username = avatar_username or username
    return (
        '<div id="app"><div id="sidebar"><div><a href="/">Home</a></div>'
        '<div><a href="/direct/inbox/">Messages</a></div>'
        f'<div><a href="/{username}/?hl=en"><img width="32" height="32" '
        f'alt="{avatar_username}\'s profile picture"></a></div></div>'
        '<main><article>Konten feed</article></main></div>'
    )


def login_form(after_submit=None, *, fields=("email", "pass")):
    outcome = after_submit if after_submit is not None else navigation()
    return (
        f'<form><input name="{fields[0]}"><input name="{fields[1]}" type="password">'
        '<button type="button" onclick="window.showClicks++">Show</button>'
        '<button type="submit">Log in</button></form>'
        '<script>window.submissions = 0; window.showClicks = 0; '
        'document.querySelector("form").onsubmit = event => {'
        'event.preventDefault(); window.submissions++; '
        f'document.body.innerHTML = {json.dumps(outcome)};'
        '};</script>'
    )


class LoginVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch(
            executable_path=config.chrome_path(), headless=True
        )
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile_dir = Path(self.temporary.name)
        await self.set_identity("1001")
        self.account = Account("fixture_user", "fixture-password")
        timing = patch.object(config, "LOGIN_RESULT_TIMEOUT_SECS", 1.5)
        timing.start()
        self.addCleanup(timing.stop)

    async def set_identity(self, user_id):
        await self.context.add_cookies([
            {"name": name, "value": value, "domain": ".instagram.com",
             "path": "/", "expires": time.time() + 60}
            for name, value in (("sessionid", "fixture-only"), ("ds_user_id", user_id))
        ])

    async def install(self, body):
        await self.context.unroute_all(behavior="wait")

        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body=body)

        await self.context.route("**/*", serve)

    async def run_login(self, *, manual_timeout=0):
        return await login(
            self.page, self.context, self.account,
            manual_timeout=manual_timeout, profile_dir=self.profile_dir,
        )

    async def show(self, body, *, manual_timeout=0):
        await self.install(body)
        return await self.run_login(manual_timeout=manual_timeout)

    async def test_inline_verification_overrides_old_cookie_and_navigation(self):
        result = await self.show(
            navigation() + '<input name="verificationCode" aria-label="Security code">'
        )
        self.assertEqual(result.status, "verification_required")
        self.assertFalse(result.reused)

    async def test_inline_heading_barrier_overrides_old_cookie_and_navigation(self):
        result = await self.show(navigation().replace(
            '<article>Konten feed</article>',
            '<section><h1>Confirm your identity</h1><button>Continue</button></section>',
        ))
        self.assertEqual(result.status, "verification_required")
        self.assertFalse(result.reused)

    async def test_feed_heading_is_not_a_login_barrier(self):
        result = await self.show(navigation().replace(
            'Konten feed', '<h1>Confirm your identity</h1><button>Continue</button>',
        ))
        self.assertEqual(result.status, "success")
        self.assertTrue(result.reused)

    async def test_hidden_and_navigation_headings_are_not_login_barriers(self):
        result = await self.show(navigation().replace(
            '<main>', '<main><section hidden><h1>Confirm your identity</h1></section>'
            '<nav><h1>Confirm your identity</h1></nav>',
        ))
        self.assertEqual(result.status, "success")
        self.assertTrue(result.reused)

    async def test_feed_caption_is_not_a_login_error(self):
        result = await self.show(
            navigation().replace("Konten feed", "Incorrect password; account suspended; check your email")
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(result.reused)

    async def test_own_avatar_mismatch_rejects_matching_feed_avatar(self):
        result = await self.show(
            navigation("different_user").replace(
                "Konten feed",
                '<a href="/fixture_user/?hl=en"><img width="32" height="32" '
                'alt="fixture_user\'s profile picture"></a>',
            )
        )
        self.assertEqual(result.status, "unexpected_page")
        self.assertFalse(result.reused)

    async def test_own_avatar_href_and_label_must_agree(self):
        result = await self.show(navigation(avatar_username="different_user"))
        self.assertEqual(result.status, "unexpected_page")

    async def test_feed_avatar_is_not_positive_own_profile_evidence(self):
        result = await self.show(
            '<div><a href="/">Home</a><a href="/direct/inbox/">Messages</a>'
            '<main><article><a href="/fixture_user/?hl=en">'
            '<img width="32" height="32" alt="fixture_user\'s profile picture">'
            '</a></article></main></div>'
        )
        self.assertEqual(result.status, "timeout")
        self.assertFalse(result.reused)

    async def test_matching_sidebar_identity_ignores_other_feed_avatars(self):
        result = await self.show(navigation().replace(
            "Konten feed", '<a href="/other_user/"><img width="32" height="32" '
            'alt="other_user\'s profile picture"></a>',
        ))
        self.assertEqual(result.status, "success")
        self.assertTrue(result.reused)

    async def test_security_alert_overrides_authenticated_navigation(self):
        result = await self.show(navigation() + '<div role="alert">Suspicious login attempt</div>')
        self.assertEqual(result.status, "verification_required")

    async def test_second_security_dialog_is_not_hidden_by_optional_first(self):
        result = await self.show(
            navigation() + '<div role="dialog">Save your login info? '
            '<button onclick="window.dismissed = true">Not now</button></div>'
            '<div role="dialog">Check your email to confirm your identity</div>'
        )
        self.assertEqual(result.status, "verification_required")
        self.assertFalse(await self.page.evaluate("Boolean(window.dismissed)"))

    async def test_unknown_blocking_dialog_is_not_success(self):
        result = await self.show(navigation() + '<div role="dialog">Action required</div>')
        self.assertEqual(result.status, "verification_required")

    async def test_not_now_button_and_link_are_actually_clicked(self):
        for tag in ("button", "a"):
            with self.subTest(role=tag):
                href = ' href="#"' if tag == "a" else ""
                result = await self.show(
                    navigation() + '<div role="dialog">Turn on notifications'
                    f'<{tag}{href} onclick="event.preventDefault(); window.dismissed = true; '
                    f'this.parentElement.remove()">Not now</{tag}></div>'
                )
                self.assertEqual(result.status, "success")
                self.assertTrue(await self.page.evaluate("window.dismissed"))

    async def test_observed_incorrect_password_stops_after_single_submit(self):
        result = await self.show(login_form('<div role="alert">The password you entered is incorrect.</div>'))
        self.assertEqual(result.status, "invalid_credentials")
        self.assertEqual(await self.page.evaluate("window.submissions"), 1)
        self.assertEqual(await self.page.evaluate("window.showClicks"), 0)

    async def test_legacy_form_submits_login_not_show_password(self):
        result = await self.show(login_form(fields=("username", "password")))
        self.assertEqual(result.status, "success")
        self.assertFalse(result.reused)
        self.assertEqual(await self.page.evaluate("window.submissions"), 1)
        self.assertEqual(await self.page.evaluate("window.showClicks"), 0)

    async def test_prelogin_manual_checkpoint_can_transition_to_single_submit(self):
        await self.install('<input name="verificationCode" aria-label="Security code">')
        task = asyncio.create_task(self.run_login(manual_timeout=3))
        try:
            await self.page.wait_for_selector('input[name="verificationCode"]')
            await asyncio.sleep(0.6)
            await self.page.set_content(login_form())
            result = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(result.status, "success")
        self.assertFalse(result.reused)
        self.assertEqual(await self.page.evaluate("window.submissions"), 1)

    async def test_manual_deadline_does_not_reset_or_resubmit_returned_form(self):
        await self.install('<input name="verificationCode" aria-label="Security code">')
        task = asyncio.create_task(self.run_login(manual_timeout=2))
        try:
            await self.page.wait_for_selector('input[name="verificationCode"]')
            await asyncio.sleep(0.6)
            await self.page.set_content(
                '<form><input name="email"><input name="pass" type="password">'
                '<button type="submit">Log in</button></form><script>window.submissions=0;'
                'document.querySelector("form").onsubmit=e=>{e.preventDefault();window.submissions++};</script>'
            )
            result = await asyncio.wait_for(task, timeout=2.5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(result.status, "verification_required")
        self.assertEqual(await self.page.evaluate("window.submissions"), 1)

    async def test_unbound_email_reuse_fails_closed(self):
        self.account = Account("fixture@example.invalid", "fixture-password")
        result = await self.show(navigation())
        self.assertEqual(result.status, "unexpected_page")
        self.assertFalse(result.reused)

    async def test_email_enrollment_reuse_then_cookie_identity_change(self):
        self.account = Account("fixture@example.invalid", "fixture-password")
        enrolled = await self.show(login_form())
        self.assertEqual(enrolled.status, "success")
        self.assertFalse(enrolled.reused)
        reused = await self.show(navigation())
        self.assertEqual(reused.status, "success")
        self.assertTrue(reused.reused)
        await self.set_identity("2002")
        changed = await self.show(navigation())
        self.assertEqual(changed.status, "unexpected_page")
        self.assertFalse(changed.reused)

    async def test_origin_drift_during_username_fill_never_fills_foreign_password(self):
        captured = []
        await self.page.expose_function("captureForeignSecret", lambda value: captured.append(value))

        async def serve(route):
            body = (
                '<input name="pass" type="password" oninput="captureForeignSecret(this.value)">'
                if route.request.url.startswith("https://outside.invalid/") else
                '<input name="email" oninput="location.replace(\'https://outside.invalid/\')">'
                '<input name="pass" type="password"><button>Log in</button>'
            )
            await route.fulfill(status=200, content_type="text/html", body=body)

        await self.context.route("**/*", serve)
        result = await self.run_login()
        self.assertNotEqual(result.status, "success")
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
