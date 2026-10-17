# create by Ortseam
"""Login UI Instagram satu kali; 2FA/checkpoint diselesaikan pemilik akun."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeoutError

from accounts import Account
import config

LOG = logging.getLogger("instagram")
USERNAME = 'input[name="email"], input[name="username"]'
PASSWORD = 'input[name="pass"], input[name="password"]'
LOGIN_BUTTON = re.compile(r"^(Log in|Masuk)$", re.I)
AUTH_NAV = 'a[href^="/direct/"], a[href="/accounts/edit/"], svg[aria-label="Home"], svg[aria-label="Beranda"]'
NOT_NOW = re.compile(r"^(Not now|Lain kali|Nanti saja)$", re.I)
DECLINE_COOKIES = re.compile(r"^(Decline optional cookies|Only allow essential cookies|Reject optional cookies|Tolak cookie opsional)$", re.I)
OPTIONAL_PROMPTS = {
    "save_info": re.compile(r"save (?:your )?login (?:info|information)|simpan info login", re.I),
    "notifications": re.compile(r"turn on notifications|aktifkan notifikasi", re.I),
}
FAILURES = (
    ("account_disabled", re.compile(r"account (?:has been |is )?(?:suspended|disabled)|we suspended your account|akun.{0,40}(?:ditangguhkan|dinonaktifkan)", re.I)),
    ("rate_limited", re.compile(r"please wait a few minutes|try again later|too many (?:requests|attempts)|tunggu beberapa menit|coba lagi nanti", re.I)),
    ("invalid_credentials", re.compile(r"(?:password|username|login information) (?:that )?(?:(?:you (?:entered|provided)|you.ve entered) (?:is |was )?|is |was )?(?:incorrect|wrong|invalid)|incorrect (?:username (?:or|and) )?password|sorry, your password|username you entered doesn.t belong|password yang anda masukkan salah|kata sandi.{0,30}salah", re.I)),
    ("verification_required", re.compile(r"enter (?:the |a )?(?:security|confirmation|authentication|6.digit) code|confirm (?:it.s|your identity)|check your (?:email|notifications)|suspicious login|unusual login attempt|help us confirm|verify (?:your identity|you.re human)|secure your account|account (?:has been |was )?compromised|review this login|was this you|masukkan kode|konfirmasi identitas", re.I)),
)
IDENTITY_FILE = "instagram_identity.json"


class _UnexpectedOrigin(Exception):
    pass


@dataclass(frozen=True)
class LoginResult:
    status: str
    detail: str
    reused: bool = False


async def _visible(locator: Locator) -> bool:
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            return True
    return False


async def _click_named(page: Page, pattern: re.Pattern, timeout_ms: float) -> bool:
    # Not now teramati sebagai link maupun button; jangan pakai tombol pertama.
    for role in ("button", "link"):
        controls = page.get_by_role(role, name=pattern)
        for index in range(await controls.count()):
            control = controls.nth(index)
            if not await control.is_visible() or not await control.is_enabled():
                continue
            handle = await control.element_handle(timeout=timeout_ms)
            if handle is None:
                continue
            try:
                if not _trusted_origin(page):
                    raise _UnexpectedOrigin
                await handle.click(timeout=timeout_ms)
                return True
            finally:
                await handle.dispose()
    return False


def _trusted_origin(page: Page) -> bool:
    url = urlsplit(page.url)
    return url.scheme == "https" and url.hostname in {"www.instagram.com", "instagram.com"} and url.port in {None, 443} and not url.username and not url.password


async def _authenticated_user_id(context: BrowserContext) -> str | None:
    cookies = await context.cookies(config.INSTAGRAM_LOGIN_URL)
    values = {
        name: {cookie["value"] for cookie in cookies if cookie["name"] == name and cookie["value"]}
        for name in ("sessionid", "ds_user_id")
    }
    if any(len(items) != 1 for items in values.values()):
        return None
    return next(iter(values["ds_user_id"]))


async def _submit_form(page: Page, account: Account, timeout_ms: float) -> None:
    for selector, value in ((USERNAME, account.identifier), (PASSWORD, account.password)):
        if not _trusted_origin(page):
            raise _UnexpectedOrigin
        handle = await page.locator(selector).filter(visible=True).first.element_handle(timeout=timeout_ms)
        if handle is None:
            raise PlaywrightTimeoutError("Form login tidak lagi tersedia.")
        try:
            # Handle terikat dokumen: navigasi tidak boleh me-resolve ulang ke
            # input baru pada origin lain di sela pengisian kedua kredensial.
            if not _trusted_origin(page):
                raise _UnexpectedOrigin
            await handle.fill(value, timeout=timeout_ms)
        finally:
            await handle.dispose()
    if not _trusted_origin(page):
        raise _UnexpectedOrigin
    button = await page.get_by_role("button", name=LOGIN_BUTTON).filter(visible=True).first.element_handle(timeout=timeout_ms)
    if button is None:
        raise PlaywrightTimeoutError("Tombol login tidak lagi tersedia.")
    try:
        if not _trusted_origin(page):
            raise _UnexpectedOrigin
        await button.click(timeout=timeout_ms)
    finally:
        await button.dispose()


async def _state(page: Page, context: BrowserContext) -> str:
    """Cookie saja bukan bukti login; seluruh penghalang UI didahulukan."""
    if not _trusted_origin(page):
        return "unexpected_page"
    path = urlsplit(page.url).path.lower()
    if any(part in path for part in ("/challenge", "/checkpoint", "/two_factor", "/confirm_email")):
        return "verification_required"
    code_input = page.locator('input[name="verificationCode"], input[name="security_code"], input[autocomplete="one-time-code"]')
    captcha = page.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[title*="challenge" i]')
    if await _visible(code_input) or await _visible(captcha):
        return "verification_required"
    authenticated = await _authenticated_user_id(context) is not None
    has_form = await _visible(page.locator(USERNAME)) or await _visible(page.locator(PASSWORD))
    has_navigation = authenticated and not has_form and await _visible(page.locator(AUTH_NAV))
    surfaces = await page.evaluate("""() => {
        const visible = el => {
            const css = getComputedStyle(el);
            return css.display !== 'none' && css.visibility !== 'hidden' && !!el.getClientRects().length;
        };
        const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], dialog[open]')].filter(visible);
        const alerts = [...document.querySelectorAll('[role="alert"]')]
            .filter(el => visible(el) && !el.closest('article, [role="article"]'));
        // Checkpoint inline dapat tinggal di main bersama shell navigasi lama.
        // Hanya heading di luar feed/navigasi, bukan seluruh teks main.
        const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')]
            .filter(el => visible(el) && el.closest('main, [role="main"]')
                && !el.closest('article, [role="article"], nav, [role="navigation"]'));
        // Teks feed tidak pernah menjadi pesan kesalahan login, termasuk saat
        // cookie kedaluwarsa tetapi shell feed belum hilang.
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const body = [];
        while (walker.nextNode()) {
            const parent = walker.currentNode.parentElement;
            if (!parent.closest('article, [role="article"], script, style, noscript') && visible(parent)) {
                body.push(walker.currentNode.textContent);
            }
        }
        return {dialogs: dialogs.map(el => el.innerText), alerts: alerts.map(el => el.innerText),
            headings: headings.map(el => el.innerText), body: body.join(' ')};
    }""")
    # Jangan hanya melihat dialog pertama: dialog kedua bisa berisi checkpoint.
    texts = surfaces["dialogs"] + surfaces["alerts"] + surfaces["headings"]
    if not has_navigation:
        texts.append(surfaces["body"])
    for status, pattern in FAILURES:
        if any(pattern.search(text) for text in texts):
            return status

    prompts: list[str] = []
    for text in surfaces["dialogs"]:
        prompt = next((name for name, pattern in OPTIONAL_PROMPTS.items() if pattern.search(text)), None)
        if prompt is None:
            return "verification_required"
        prompts.append(prompt)
    # Alert yang tidak dikenali juga belum merupakan bukti session siap dipakai.
    if surfaces["alerts"]:
        return "verification_required"
    if authenticated and not has_form:
        if prompts:
            return prompts[0]
        if "onetap" in path:
            return "save_info"
        if not has_navigation:
            for name, pattern in OPTIONAL_PROMPTS.items():
                if pattern.search(surfaces["body"]):
                    return name
        if has_navigation:
            return "success"
    if prompts:
        return "verification_required"
    if has_form and await _visible(page.locator(USERNAME)) and await _visible(page.locator(PASSWORD)):
        return "form"
    return "loading"


async def _own_profile(page: Page) -> tuple[set[str], bool]:
    evidence = await page.evaluate("""() => {
        const visible = el => {
            const css = getComputedStyle(el);
            return css.display !== 'none' && css.visibility !== 'hidden' && !!el.getClientRects().length;
        };
        const pathOf = link => {
            try {
                const url = new URL(link.href, location.href);
                return url.origin === location.origin ? url.pathname.toLowerCase() : '';
            } catch { return ''; }
        };
        const navKind = link => {
            const path = pathOf(link);
            if (path === '/') return 'home';
            if (path.startsWith('/direct/')) return 'direct';
            if (path.startsWith('/explore/')) return 'explore';
            if (path === '/reels/' || path === '/reels') return 'reels';
            if (path === '/accounts/edit/') return 'settings';
            return '';
        };
        const inOwnNavigation = link => {
            if (link.closest('article, [role="article"], [role="dialog"], [role="alertdialog"]')) return false;
            for (let parent = link.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
                // Jangan naik sampai app root yang juga memuat avatar pengguna feed.
                if (parent.matches('main, [role="main"], article, [role="article"]') ||
                    parent.querySelector('main, [role="main"], article, [role="article"]')) return false;
                const kinds = new Set([...parent.querySelectorAll('a[href]')].filter(visible).map(navKind).filter(Boolean));
                if (kinds.size >= 2) return true;
            }
            return false;
        };
        const usernames = new Set();
        let conflict = false;
        for (const link of document.querySelectorAll('a[href]')) {
            if (!visible(link)) continue;
            const match = pathOf(link).match(/^\\/([a-z0-9_.]+)\\/?$/i);
            if (!match || /^(accounts|direct|explore|reels|stories|p|reel|about|legal)$/.test(match[1])) continue;
            const labels = [link.getAttribute('aria-label'), link.textContent,
                ...[...link.querySelectorAll('[alt], [aria-label]')].filter(visible)
                    .flatMap(el => [el.getAttribute('alt'), el.getAttribute('aria-label')])]
                .filter(Boolean).map(text => text.trim());
            const avatars = labels.map(text => text.match(/^([a-z0-9_.]+)['’]s profile picture$/i)).filter(Boolean);
            if (!avatars.length && !labels.some(text => /^(profile|profil)$/i.test(text))) continue;
            if (!inOwnNavigation(link)) continue;
            if (avatars.some(avatar => avatar[1].toLowerCase() !== match[1])) conflict = true;
            usernames.add(match[1]);
        }
        return {usernames: [...usernames], conflict};
    }""")
    return set(evidence["usernames"]), evidence["conflict"]


def _read_identity(profile_dir: Path | None, identifier: str) -> str | None:
    if profile_dir is None:
        return None
    try:
        with (profile_dir / IDENTITY_FILE).open(encoding="utf-8") as source:
            identity = json.load(source)
    except FileNotFoundError:
        return None
    if (
        not isinstance(identity, dict)
        or set(identity) != {"version", "identifier", "ds_user_id"}
        or type(identity["version"]) is not int
        or identity["version"] != 1
        or identity["identifier"] != identifier.casefold()
        or not isinstance(identity["ds_user_id"], str)
        or not identity["ds_user_id"]
    ):
        raise ValueError("Ikatan identitas profil tidak valid.")
    return identity["ds_user_id"]


def _bind_identity(profile_dir: Path, identifier: str, user_id: str) -> None:
    # Dipanggil hanya saat open_session masih memegang kunci profil. Tidak
    # menyimpan password/sessionid; kegagalan tulis tidak boleh menjadi sukses.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=profile_dir, prefix=".identity-", suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            json.dump({"version": 1, "identifier": identifier.casefold(), "ds_user_id": user_id}, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, profile_dir / IDENTITY_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


DETAILS = {
    "invalid_credentials": "Instagram menolak kredensial; tidak dilakukan percobaan ulang otomatis.",
    "rate_limited": "Instagram membatasi percobaan. Hentikan dan coba lagi setelah pembatasan selesai.",
    "account_disabled": "Instagram menampilkan akun dinonaktifkan/ditangguhkan; tangani lewat jalur resmi.",
    "verification_required": "Verifikasi manual belum selesai. Buka kembali profil akun dan selesaikan 2FA/checkpoint.",
    "unexpected_page": "Halaman bukan origin Instagram yang diizinkan; kredensial tidak dikirim.",
    "timeout": "Batas waktu tercapai tanpa bukti login yang lengkap; bukan sukses.",
}


async def login(
    page: Page,
    context: BrowserContext,
    account: Account,
    *,
    manual_timeout: float = config.MANUAL_TIMEOUT_SECS,
    profile_dir: Path | None = None,
) -> LoginResult:
    """Submit maksimal sekali; profile_dir harus masih dikunci oleh open_session."""
    try:
        pinned_user_id = _read_identity(profile_dir, account.identifier)
    except (OSError, ValueError):
        return LoginResult("unexpected_page", "Ikatan identitas profil tidak dapat dibaca; login dihentikan.")
    LOG.info("[%s] Membuka Instagram; memeriksa session tersimpan.", account.identifier)
    submitted = False
    used_manual = False
    dismissed: set[str] = set()
    deadline = time.monotonic() + config.LOGIN_RESULT_TIMEOUT_SECS
    manual_deadline: float | None = None

    def remaining() -> float:
        return max(0.0, (manual_deadline if manual_deadline is not None else deadline) - time.monotonic())

    try:
        response = await page.goto(config.INSTAGRAM_LOGIN_URL, wait_until="domcontentloaded", timeout=config.TIMEOUT_NAVIGATE_MS)
        if response and response.status == 429:
            return LoginResult("rate_limited", DETAILS["rate_limited"])
        if response and response.status >= 400:
            return LoginResult("unexpected_page", f"Instagram mengembalikan HTTP {response.status}; form tidak dikirim.")
        deadline = time.monotonic() + config.LOGIN_RESULT_TIMEOUT_SECS
        while remaining() > 0:
            if page.is_closed():
                return LoginResult("verification_required" if used_manual else "timeout", "Jendela browser ditutup sebelum login terverifikasi.")
            if not _trusted_origin(page):
                raise _UnexpectedOrigin
            state = await asyncio.wait_for(_state(page, context), timeout=remaining())
            if state in {"invalid_credentials", "rate_limited", "account_disabled", "unexpected_page"}:
                return LoginResult(state, DETAILS[state])

            if state == "success":
                usernames, conflict = await asyncio.wait_for(_own_profile(page), timeout=remaining())
                username_input = bool(re.fullmatch(r"[A-Za-z0-9_.]+", account.identifier)) and not account.identifier.isdigit()
                if conflict or len(usernames) > 1 or (username_input and usernames and usernames != {account.identifier.casefold()}):
                    return LoginResult("unexpected_page", "Profil aktif tidak cocok dengan username input atau bukti identitas bertentangan; tidak melakukan logout otomatis.")
                if username_input and not usernames:
                    # Navigasi bisa selesai sebelum kontrol profil dirender.
                    await asyncio.sleep(min(0.5, remaining()))
                    continue
                user_id = await asyncio.wait_for(_authenticated_user_id(context), timeout=remaining())
                if user_id is None:
                    continue
                if pinned_user_id is not None and pinned_user_id != user_id:
                    return LoginResult("unexpected_page", "Identitas akun berubah dari ikatan profil tersimpan; login dihentikan.")
                if not username_input and pinned_user_id is None and (not submitted or profile_dir is None):
                    return LoginResult("unexpected_page", "Email/telepon belum terikat ke identitas akun. Session lama tidak cukup; perlu login form pertama pada profil yang dikunci.")
                if not _trusted_origin(page):
                    raise _UnexpectedOrigin
                if await asyncio.wait_for(_state(page, context), timeout=remaining()) != "success":
                    continue
                if profile_dir is not None and pinned_user_id is None:
                    try:
                        _bind_identity(profile_dir, account.identifier, user_id)
                    except (OSError, ValueError):
                        return LoginResult("unexpected_page", "Ikatan identitas profil gagal disimpan; login tidak dinyatakan sukses.")
                if remaining() <= 0:
                    break
                reused = not submitted and not used_manual
                return LoginResult("success", "Session tersimpan dan identitas akun masih valid." if reused else "Login terverifikasi: cookie autentikasi, identitas akun, dan navigasi tersedia tanpa penghalang.", reused)

            timeout_ms = max(1.0, min(config.TIMEOUT_ELEMENT_MS, remaining() * 1000))
            if state in OPTIONAL_PROMPTS and state not in dismissed:
                if await asyncio.wait_for(_click_named(page, NOT_NOW, timeout_ms), timeout=remaining()):
                    dismissed.add(state)
                    continue
            if "cookies" not in dismissed:
                # Menolak cookie opsional saja, tidak menerima semuanya.
                if await asyncio.wait_for(_click_named(page, DECLINE_COOKIES, timeout_ms), timeout=remaining()):
                    dismissed.add("cookies")
                    continue
            if state == "verification_required":
                if manual_deadline is None:
                    used_manual = True
                    if manual_timeout <= 0:
                        return LoginResult(state, DETAILS[state])
                    manual_deadline = time.monotonic() + manual_timeout
                    LOG.warning("[%s] Selesaikan 2FA/checkpoint/CAPTCHA secara manual di browser (maks %.0f detik). Jangan kirim kode ke log/chat.", account.identifier, manual_timeout)
            elif state == "form" and not submitted:
                LOG.info("[%s] Mengisi form dan mengirim satu percobaan login.", account.identifier)
                # Checkpoint sebelum form tidak menghabiskan jatah submit.
                # Jatah dikonsumsi sebelum klik, sehingga error ambigu tidak diulang.
                submitted = True
                await asyncio.wait_for(_submit_form(page, account, timeout_ms), timeout=remaining())
                if not _trusted_origin(page):
                    raise _UnexpectedOrigin
                if manual_deadline is None:
                    deadline = time.monotonic() + config.LOGIN_RESULT_TIMEOUT_SECS
            await asyncio.sleep(min(0.5, remaining()))
    except _UnexpectedOrigin:
        return LoginResult("unexpected_page", DETAILS["unexpected_page"])
    except (TimeoutError, PlaywrightError):
        # Jangan bocorkan exception fill() yang bisa membawa isi password.
        if not _trusted_origin(page):
            return LoginResult("unexpected_page", DETAILS["unexpected_page"])
    status = "verification_required" if used_manual else "timeout"
    return LoginResult(status, DETAILS[status])
