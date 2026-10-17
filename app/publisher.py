# create by Ortseam
"""Composer web Instagram: persiapan terukur, Share tunggal, hasil ambigu ditahan."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from playwright.async_api import Page, Locator, Error as PlaywrightError

import config
from models import DraftValidationError, Job, MediaItem, PublishResult, UnsupportedOption


async def _visible(locator: Locator) -> Locator | None:
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            return candidate
    return None


async def _dismiss_video_notice(page: Page) -> bool:
    notice = await _visible(page.get_by_role("dialog").filter(has=page.get_by_role(
        "heading", name="Video posts are now shared as reels", exact=True)))
    if notice is None:
        return False
    await notice.get_by_role("button", name="OK", exact=True).click()
    await notice.wait_for(state="hidden")
    return True


async def _button(page: Page, name: str, *, required: bool = True) -> bool:
    await _dismiss_video_notice(page)
    control = await _visible(page.get_by_role("button", name=name, exact=True))
    if control is None:
        control = await _visible(page.get_by_role("link", name=name, exact=True))
    if control is None:
        if required:
            raise UnsupportedOption(f"Control '{name}' tidak tersedia pada halaman ini.")
        return False
    await control.click()
    return True


def _instagram(page: Page) -> None:
    url = urlsplit(page.url)
    if url.scheme != "https" or url.hostname not in {"www.instagram.com", "instagram.com"}:
        raise DraftValidationError("Halaman meninggalkan origin Instagram; operasi dibatalkan.")


async def _dialog(page: Page) -> Locator:
    await _dismiss_video_notice(page)
    dialogs = page.get_by_role("dialog").filter(has=page.get_by_role("button", name=re.compile(r"^(Next|Share)$")))
    dialog = await _visible(dialogs)
    if dialog is None:
        raise DraftValidationError("Composer Instagram tidak tersedia.")
    return dialog


async def _stage(page: Page, heading: str) -> None:
    await page.get_by_role("heading", name=heading, exact=True).wait_for(state="visible")
    _instagram(page)


async def _next(page: Page, heading: str) -> None:
    await _button(page, "Next")
    await _stage(page, heading)


async def _range(slider: Locator, value: float) -> None:
    # Native range memakai setter input agar React menerima event input/change.
    await slider.evaluate("""(el, value) => {
      if (el.tagName !== 'INPUT' || el.type !== 'range') throw Error('not native range');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      setter.call(el,String(value));
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
    }""", value)
    if abs(float(await slider.input_value()) - value) > 0.1:
        raise DraftValidationError("Nilai slider tidak sesuai permintaan.")


async def _switch(dialog: Locator, label: str, checked: bool) -> None:
    text = await _visible(dialog.get_by_text(label, exact=True))
    if text is None:
        raise UnsupportedOption(f"Setting '{label}' tidak tersedia.")
    # Switch pada UI ini tidak memiliki accessible name: cari ancestor terkecil
    # yang berisi label ini dan tepat satu control, bukan nth global.
    control = text.locator("xpath=ancestor::*[.//input[@role='switch']][1]").locator('input[role="switch"]')
    if await control.count() != 1:
        raise UnsupportedOption(f"Setting '{label}' ambigu pada UI saat ini.")
    await control.set_checked(checked)
    if await control.is_checked() != checked:
        raise DraftValidationError(f"Setting '{label}' tidak tersimpan pada draft.")


async def _change_item(page: Page, direction: str = "Right chevron") -> None:
    dialog = await _dialog(page)
    candidates = dialog.get_by_role("button", name=direction, exact=True)
    # Saat transisi, Instagram mempertahankan chevron lama di bawah overlay.
    # Pilih control yang benar-benar menerima pointer, bukan force-click yang
    # menutupi bug state atau nth pertama dari carousel sebelumnya.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for index in range(await candidates.count() - 1, -1, -1):
            button = candidates.nth(index)
            if not await button.is_visible():
                continue
            uncovered = await button.evaluate("""el => {
              const r=el.getBoundingClientRect();
              const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
              return !!top && el.contains(top);
            }""")
            if uncovered:
                await button.click()
                await asyncio.sleep(0.25)
                return
        await asyncio.sleep(0.1)
    raise DraftValidationError("Navigasi item carousel tidak dapat dijangkau.")


CROP_MENU = re.compile(r"^(Original|1:1|4:5|9:16|16:9)(?:\s|$)")


async def _crop_frame_ratios(dialog: Locator) -> list[float]:
    # Media mempertahankan rasio sumber. Bingkai pemotong gambar ada di parent,
    # sedangkan video carousel memiliki satu wrapper tambahan sebelum bingkai.
    return await dialog.evaluate("""d => [...d.querySelectorAll('[style*="background-image"],video')]
      .flatMap(e => {
        if (!e.getClientRects().length) return [];
        for (let p=e.parentElement;p && p!==d;p=p.parentElement) {
          const r=p.getBoundingClientRect(), s=getComputedStyle(p);
          if (r.width>200 && r.height>200 &&
              [s.overflowX,s.overflowY].some(v=>v==='hidden'||v==='clip')) return [r.width/r.height];
        }
        return [];
      })""")


async def _crop(page: Page, item: MediaItem) -> None:
    dialog = await _dialog(page)
    if item.crop is not None:
        await dialog.locator('button:has(svg[aria-label="Select crop"])').click()
        option = await _visible(dialog.get_by_role("button", name=re.compile(r"^" + re.escape(item.crop) + r"(?:\s|$)")))
        if option is None:
            raise UnsupportedOption(f"Rasio crop '{item.crop}' tidak tersedia.")
        await option.click()
        if item.crop != "Original":
            numerator, denominator = (float(part) for part in item.crop.split(":"))
            wanted = numerator / denominator
            # Bingkai crop beranimasi ~1 detik. Terima hanya setelah rasio cocok
            # DAN stabil 300 ms; ukuran sesaat di tengah animasi bukan bukti.
            deadline = time.monotonic() + 4
            stable_since: float | None = None
            while time.monotonic() < deadline:
                matched = any(abs(ratio - wanted) < 0.025 for ratio in await _crop_frame_ratios(dialog))
                now = time.monotonic()
                if not matched:
                    stable_since = None
                elif stable_since is None:
                    stable_since = now
                elif now - stable_since >= 0.3:
                    break
                await asyncio.sleep(0.1)
            else:
                raise DraftValidationError("Geometri crop tidak cocok dengan rasio job.")
        # Menu crop tetap terbuka setelah memilih rasio dan menelan klik tombol
        # lain (zoom) selama animasi; tutup dulu dengan mengklik tombol crop lagi.
        if await _visible(dialog.get_by_role("button", name=CROP_MENU)) is not None:
            await dialog.locator('button:has(svg[aria-label="Select crop"])').click()
            await dialog.get_by_role("button", name=CROP_MENU).first.wait_for(state="hidden")
    if item.zoom is not None:
        control = await _visible(dialog.locator('button:has(svg[aria-label="Select zoom"])'))
        if control is None:
            raise UnsupportedOption("Zoom tidak tersedia untuk media ini.")
        await control.click()
        slider = dialog.locator('input[type="range"]')
        await slider.first.wait_for(state="visible")
        await _range(slider.first, item.zoom)
        await control.click()
        await slider.first.wait_for(state="hidden")


async def _edit_photo(page: Page, item: MediaItem) -> None:
    dialog = await _dialog(page)
    if item.filter_name is not None:
        await dialog.get_by_role("tab", name="Filters", exact=True).click()
        filter_name = "Normal" if item.filter_name == "Original" else item.filter_name
        button = await _visible(dialog.get_by_role("button", name=re.compile(r"^Filter: " + re.escape(filter_name) + r"\b")))
        if button is None:
            raise UnsupportedOption("Filter yang diminta tidak tersedia.")
        await button.click()
        if item.filter_name != "Original":
            # Filter aktif memunculkan slider intensitas. Control global yang
            # tidak berubah bukan bukti filter telah diterapkan.
            intensity = dialog.locator('input[type="range"]')
            await intensity.first.wait_for(state="visible")
    if item.adjustments:
        await dialog.get_by_role("tab", name="Adjustments", exact=True).click()
        for name, value in item.adjustments.items():
            label = dialog.get_by_text(name.title(), exact=True)
            parent = label.locator("xpath=ancestor::*[.//input[@type='range']][1]")
            slider = parent.locator('input[type="range"]')
            if await slider.count() != 1:
                raise UnsupportedOption(f"Adjustment '{name}' tidak tersedia/ambigu.")
            await _range(slider, value)


async def _settled(locator: Locator, *, timeout: float = 4.0) -> None:
    # Jangan menerima dua sampel sebelum pergeseran panel Edit yang tertunda.
    # Ukuran dan posisi perlu diam terus-menerus sebelum drag timeline dimulai.
    deadline = time.monotonic() + timeout
    previous = None
    stable_since = None
    while time.monotonic() < deadline:
        box = await locator.bounding_box()
        now = time.monotonic()
        if box and previous and all(abs(box[key] - previous[key]) < 0.5 for key in ("x", "y", "width", "height")):
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= 0.6:
                return
        else:
            stable_since = None
        previous = box
        await asyncio.sleep(0.1)
    raise DraftValidationError("Timeline belum stabil; tidak menebak posisi cover/trim.")


async def _timeline_value(page: Page, slider: Locator, percent: float) -> None:
    # Timeline Instagram melaporkan persen, BUKAN detik. Drag mengukur arah dan
    # skala handle; nilai akhir wajib dibaca kembali sebelum Share.
    minimum = await slider.get_attribute("aria-valuemin")
    maximum = await slider.get_attribute("aria-valuemax")
    if minimum != "0" or maximum != "100":
        raise UnsupportedOption("Skala timeline berubah; tidak menebak waktu cover/trim.")
    current = float(await slider.get_attribute("aria-valuenow") or "nan")
    track = await slider.evaluate("""el => {
      for (let p=el.parentElement;p;p=p.parentElement) {
        const r=p.getBoundingClientRect();
        if(r.width>150 && r.height>=40 && r.height<220) return {x:r.x,y:r.y,width:r.width,height:r.height};
      }
      return null;
    }""")
    if not track:
        raise UnsupportedOption("Track timeline tidak dapat diukur.")
    box = await slider.bounding_box()
    if not box:
        raise UnsupportedOption("Timeline tidak terlihat.")
    # Terukur pada composer nyata: handle mengikuti kursor dikurangi 3 px ambang
    # drag (geser 36 px → handle bergerak 33 px; geser < 4 px diabaikan). Nilai
    # persen = posisi handle × 100/(lebar track − lebar handle).
    dead_zone = 3.0
    slope = 100 / max(1.0, track["width"] - box["width"])
    tolerance = 0.6
    if abs(current - percent) <= tolerance:
        return
    for _ in range(8):
        box = await slider.bounding_box()
        if not box:
            break
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        travel = (percent - current) / slope
        if abs(travel) < 0.5:
            break
        direction = 1 if travel > 0 else -1
        cursor = x + travel + direction * dead_zone
        cursor = min(track["x"] + track["width"] - 1, max(track["x"] + 1, cursor))
        await page.mouse.move(x, y)
        await page.mouse.down()
        await page.mouse.move(cursor, y, steps=10)
        await asyncio.sleep(0.15)  # tanpa jeda, posisi terakhir belum diproses saat mouse dilepas
        await page.mouse.up()
        await asyncio.sleep(0.15)
        updated = float(await slider.get_attribute("aria-valuenow") or "nan")
        if abs(updated - percent) <= tolerance:
            return
        moved = await slider.bounding_box()
        travelled = (moved["x"] + moved["width"] / 2 - x) if moved else 0
        if abs(travelled) >= 2 and abs(updated - current) >= 0.01:
            slope = (updated - current) / travelled
        current = updated
    raise UnsupportedOption("Waktu cover/trim belum sesuai permintaan; Share dibatalkan.")


async def _edit_reel(page: Page, job: Job) -> None:
    dialog = await _dialog(page)
    if job.cover_path:
        upload = dialog.locator('input[type="file"][accept*="image/jpeg"]')
        await upload.set_input_files(str(job.cover_path))
    sliders = dialog.get_by_role("slider").filter(visible=True)
    if job.cover_time is not None or job.trim_start is not None:
        duration = await dialog.locator("video").first.evaluate("v => v.duration")
        if not duration or not isinstance(duration, (int, float)) or duration <= 0:
            raise DraftValidationError("Durasi video belum terbaca.")
        await _settled(sliders.first)
        if job.cover_time is not None:
            if not 0 <= job.cover_time <= duration:
                raise DraftValidationError("Waktu cover di luar durasi media.")
            await _timeline_value(page, sliders.nth(0), job.cover_time / duration * 100)
        if job.trim_start is not None:
            if job.trim_end is None or not 0 <= job.trim_start < job.trim_end <= duration:
                raise DraftValidationError("Rentang trim di luar durasi media.")
            if await sliders.count() < 3:
                raise UnsupportedOption("Timeline trim tidak tersedia.")
            await _timeline_value(page, sliders.nth(1), job.trim_start / duration * 100)
            # Handle kanan melaporkan offset negatif dari akhir media.
            await _timeline_value(page, sliders.nth(2), (job.trim_end / duration - 1) * 100)
    if job.keep_original_audio is not None:
        audio = await _visible(dialog.locator('input[role="switch"], input[type="checkbox"]'))
        if audio is None:
            raise UnsupportedOption("Pilihan audio yang diminta tidak tersedia.")
        if not await audio.is_enabled():
            no_audio = await _visible(dialog.get_by_text("Video has no audio", exact=True))
            # Mempertahankan audio asli pada video tanpa trek suara berarti tetap
            # senyap. Hanya terima bila Instagram menyatakannya secara eksplisit.
            if no_audio is None:
                raise UnsupportedOption("Pilihan audio tidak dapat diterapkan atau status suara tidak diketahui.")
        else:
            await audio.set_checked(job.keep_original_audio)
            if await audio.is_checked() != job.keep_original_audio:
                raise DraftValidationError("Pilihan audio tidak sesuai draft.")


async def _select_username(page: Page, username: str, *, reel: bool = False) -> None:
    dialog = await _dialog(page)
    before = await dialog.get_by_text(username, exact=True).filter(visible=True).count()
    search = page.locator('input[name="userSearchInput"]')
    await search.wait_for(state="visible")
    await search.fill(username)
    match = page.get_by_text(username, exact=True).filter(visible=True)
    # Search results bisa berada di portal di luar role=dialog; kandidat harus
    # memiliki ancestor clickable, bukan caption/username akun di composer.
    candidate = match.locator("xpath=ancestor::*[@role='button' or self::button][1]")
    await candidate.first.wait_for(state="visible")
    if await candidate.count() != 1:
        raise DraftValidationError("Kandidat tag tidak unik; tidak memilih akun secara tebakan.")
    await candidate.click()
    await search.wait_for(state="hidden")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if reel:
            # Reel menampilkan hasil pilihan pada portal di luar dialog composer.
            # Baris harus memuat username tepat DAN tombol hapus tag, bukan saran.
            title = await _visible(page.get_by_text("Tagged people", exact=True))
            if title is not None:
                selected = title.locator("xpath=ancestor::*[.//*[@aria-label='Remove tag']][1]")
                if await selected.get_by_text(username, exact=True).filter(visible=True).count() == 1:
                    await dialog.get_by_role("heading").first.click()
                    await title.wait_for(state="hidden")
                    return
        elif await dialog.get_by_text(username, exact=True).filter(visible=True).count() > before:
            return
        await asyncio.sleep(0.1)
    raise DraftValidationError("Tag terpilih tidak muncul pada draft; Share dibatalkan.")


async def _tag_media(page: Page, job: Job) -> None:
    if job.kind == "reel":
        for tag in job.people_tags:
            dialog = await _dialog(page)
            control = await _visible(dialog.get_by_role("button", name="Tags Tag people", exact=True))
            if control is None:
                raise UnsupportedOption("Tag people Reel tidak tersedia.")
            await control.click()
            await _select_username(page, tag.username, reel=True)
        return
    for index, item in enumerate(job.media):
        if index:
            await _change_item(page)
        for tag in item.people_tags:
            dialog = await _dialog(page)
            area = dialog.locator('button[aria-label="Tag people"]')
            box = await area.bounding_box()
            if box is None:
                raise UnsupportedOption("Area tag foto tidak tersedia.")
            await area.click(position={"x": box["width"] * (tag.x if tag.x is not None else 0.5), "y": box["height"] * (tag.y if tag.y is not None else 0.5)})
            await _select_username(page, tag.username)
    for _ in range(max(0, len(job.media) - 1)):
        await _change_item(page, "Left chevron")


async def _search_metadata(page: Page, label: str, expected: str) -> None:
    dialog = await _dialog(page)
    field = dialog.get_by_role("textbox", name=label, exact=True)
    before = await dialog.get_by_text(expected, exact=True).filter(visible=True).count()
    await field.fill(expected)
    candidate = page.get_by_text(expected, exact=True).filter(visible=True).locator("xpath=ancestor::*[@role='button' or self::button][1]")
    await candidate.first.wait_for(state="visible")
    if await candidate.count() != 1:
        raise DraftValidationError(f"Pilihan {label} tidak unik; job dibatalkan.")
    await candidate.click()
    if label == "Add collaborators":
        picker = page.get_by_role("dialog").filter(has=page.get_by_text("Collaborators", exact=True))
        if await picker.count() != 1:
            raise DraftValidationError("Dialog kolaborator tidak dapat dikonfirmasi.")
        await picker.get_by_role("button", name="Done", exact=True).click()
        await picker.wait_for(state="hidden")
    # Location terpilih tetap tampil di input. Collaborator muncul sebagai chip.
    if label == "Add location" and (await field.input_value()).casefold() != expected.casefold():
        raise DraftValidationError("Lokasi terpilih tidak cocok dengan input job.")
    if label == "Add collaborators":
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if await dialog.get_by_text(expected, exact=True).filter(visible=True).count() > before:
                return
            await asyncio.sleep(0.1)
        raise DraftValidationError("Kolaborator tidak muncul pada draft; Share dibatalkan.")


async def prepare(page: Page, job: Job) -> None:
    _instagram(page)
    create = page.locator('a:has(svg[aria-label="New post"])')
    await create.first.wait_for(state="visible")
    await create.first.click()
    # Sebagian sesi membuat input file hanya saat tombol upload ditekan.
    # File chooser adalah kontrak browser, bukan input React yang sementara.
    async with page.expect_file_chooser() as chooser:
        await page.get_by_role("button", name="Select from computer", exact=True).click()
    await (await chooser.value).set_files([str(item.path) for item in job.media])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        notice = await _dismiss_video_notice(page)
        if not notice and await _visible(page.get_by_role("heading", name="Crop", exact=True)) is not None:
            break
        await asyncio.sleep(0.2)
    else:
        raise DraftValidationError("Media tidak masuk tahap Crop; periksa format dan batas Instagram.")
    for index, item in enumerate(job.media):
        if index:
            await _change_item(page)
        await _crop(page, item)
    for _ in range(max(0, len(job.media) - 1)):
        await _change_item(page, "Left chevron")
    await _next(page, "Edit")
    if job.kind == "reel":
        await _edit_reel(page, job)
    else:
        for index, item in enumerate(job.media):
            if index:
                await _change_item(page)
            await _edit_photo(page, item)
        for _ in range(max(0, len(job.media) - 1)):
            await _change_item(page, "Left chevron")
    await _next(page, "New reel" if job.kind == "reel" else "Create new post")
    dialog = await _dialog(page)
    caption = dialog.get_by_role("textbox", name="Add a caption...", exact=True)
    await caption.fill(job.caption)
    actual = (await caption.inner_text()).replace("\r\n", "\n").rstrip("\n")
    if actual != job.caption.replace("\r\n", "\n").rstrip("\n"):
        raise DraftValidationError("Caption pada draft tidak cocok dengan job.")
    # Hashtag di akhir caption memunculkan saran yang menutupi switch/metadata.
    # Pindahkan fokus melalui heading netral, bukan force-click atau Escape.
    await dialog.get_by_role("heading").first.click()
    if job.location:
        await _search_metadata(page, "Add location", job.location)
    for collaborator in job.collaborators:
        await _search_metadata(page, "Add collaborators", collaborator)
    if job.people_tags or any(item.people_tags for item in job.media):
        await _tag_media(page, job)
    if job.ai_label is not None:
        await _switch(dialog, "Add AI label", job.ai_label)
    if any(item.alt_text for item in job.media):
        await dialog.get_by_role("button", name=re.compile(r"^Accessibility\b")).click()
        fields = dialog.get_by_role("textbox", name="Write alt text...", exact=True)
        if await fields.count() != len(job.media):
            raise UnsupportedOption("Jumlah field alt text tidak cocok dengan media.")
        for index, item in enumerate(job.media):
            await fields.nth(index).fill(item.alt_text)
            if await fields.nth(index).input_value() != item.alt_text:
                raise DraftValidationError("Alt text tidak sesuai pada draft.")
    if job.comments_enabled is not None or job.hide_like_view_counts is not None:
        await dialog.get_by_role("button", name=re.compile(r"^Advanced settings\b")).click()
        if job.comments_enabled is not None:
            await _switch(dialog, "Turn off commenting", not job.comments_enabled)
        if job.hide_like_view_counts is not None:
            await _switch(dialog, "Hide like and view counts on this post", job.hide_like_view_counts)
    _instagram(page)
    if not await dialog.get_by_role("button", name="Share", exact=True).is_enabled():
        raise DraftValidationError("Share belum siap setelah persiapan draft.")


CONFIGURE_PATH = re.compile(r"^/api/v1/media/configure(?:_sidecar|_to_clips)?/?$")
SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")


def _configure_response(response) -> bool:
    """POST configure milik halaman sendiri ke origin Instagram; respons inilah bukti server."""
    url = urlsplit(response.url)
    return (
        response.request.method == "POST" and url.scheme == "https"
        and url.hostname in {"www.instagram.com", "instagram.com"} and CONFIGURE_PATH.match(url.path) is not None
    )


async def _configure_verdict(response, job: Job) -> tuple[bool, str | None, str | None]:
    """(diterima, permalink, pesan server). Body tidak terbaca/bukan JSON → bukan bukti."""
    try:
        payload = await response.json()
    except (PlaywrightError, ValueError):
        return False, None, None
    if not isinstance(payload, dict):
        return False, None, None
    message = payload.get("message")
    message = message[:200] if isinstance(message, str) and message else None
    if not response.ok or payload.get("status") != "ok":
        return False, None, message
    media = payload.get("media")
    code = media.get("code") if isinstance(media, dict) else None
    if not isinstance(code, str) or not SHORTCODE.fullmatch(code):
        return False, None, message
    permalink = f"https://www.instagram.com/{'reel' if job.kind == 'reel' else 'p'}/{code}/"
    return True, permalink, message


async def publish(page: Page, job: Job, before_share: Callable[[], None]) -> PublishResult:
    _instagram(page)
    dialog = await _dialog(page)
    share = dialog.get_by_role("button", name="Share", exact=True)
    await share.wait_for(state="visible")
    if not await share.is_enabled():
        raise DraftValidationError("Share tidak aktif; belum ada submit.")
    handle = await share.element_handle()
    if handle is None:
        raise DraftValidationError("Control Share berubah sebelum intent; tidak ada submit.")
    try:
        _instagram(page)
        before_share()  # Persist intent sebelum mutasi; kegagalan storage mencegah klik.
    except BaseException:
        await handle.dispose()
        raise
    deadline = time.monotonic() + config.PUBLISH_TIMEOUT_SECS
    responses: list[asyncio.Task[tuple[bool, str | None, str | None]]] = []

    async def read_verdict(response) -> tuple[bool, str | None, str | None]:
        try:
            return await asyncio.wait_for(_configure_verdict(response, job), timeout=max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            return False, None, None

    def collect(response) -> None:
        if _configure_response(response):
            responses.append(asyncio.create_task(read_verdict(response)))

    page.on("response", collect)
    server_message = None

    def verdict() -> PublishResult | None:
        nonlocal server_message
        for index in range(len(responses) - 1, -1, -1):
            task = responses[index]
            if not task.done():
                continue
            responses.pop(index)
            accepted, permalink, message = task.result()
            if accepted:
                return PublishResult("published", "Instagram menjawab configure status ok (media dibuat); permalink dari kode media.", permalink)
            server_message = message or server_message
        return None

    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DraftValidationError("Batas waktu publikasi habis sebelum klik Share.")
        await handle.click(timeout=remaining * 1000)
        _instagram(page)
        completion = page.get_by_role("dialog").get_by_text(re.compile(r"^(Your (post|reel) has been shared[.!]?|Post shared|Reel shared)$", re.I))
        confirmed_at = None
        while time.monotonic() < deadline and not page.is_closed():
            _instagram(page)
            if result := verdict():
                return result
            if confirmed_at is None and await _visible(completion) is not None:
                confirmed_at = time.monotonic()
            if confirmed_at is not None and (time.monotonic() - confirmed_at >= 1 or time.monotonic() + 0.5 >= deadline):
                # Konfirmasi dialog tanpa respons configure yang terbaca: tetap published; permalink dari tautan dialog bila ada.
                links = page.locator('[role="dialog"] a[href*="/p/"], [role="dialog"] a[href*="/reel/"]')
                link = await _visible(links)
                permalink = await link.get_attribute("href") if link else None
                if permalink and permalink.startswith("/"):
                    permalink = "https://www.instagram.com" + permalink
                return PublishResult("published", "Instagram menampilkan konfirmasi publikasi; permalink dicatat bila tersedia di dialog.", permalink)
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        # Respons yang tiba tepat sebelum batas waktu tetap bukti server.
        if result := verdict():
            return result
    except (PlaywrightError, DraftValidationError):
        # Error navigasi setelah klik tidak membatalkan bukti server yang sudah
        # selesai diterima. Jangan menunggu respons baru atau mengklik ulang.
        if result := verdict():
            return result
    finally:
        page.remove_listener("response", collect)
        for task in responses:
            task.cancel()
        if responses:
            await asyncio.gather(*responses, return_exceptions=True)
        await handle.dispose()
    detail = "Share sudah diupayakan tetapi hasil tidak pasti. Periksa profil sebelum rekonsiliasi; jangan ulang otomatis."
    if server_message:
        detail += f" Jawaban Instagram: {server_message}"
    return PublishResult("unknown_after_submit", detail)


async def discard(page: Page) -> None:
    if page.is_closed():
        return
    for attempt in range(4):
        await _dismiss_video_notice(page)
        dialog = await _visible(page.get_by_role("dialog"))
        if dialog is None:
            return
        confirmation = page.get_by_role("button", name="Discard", exact=True)
        # Escape dulu; setelah fokus berpindah ke widget lokasi/kolaborator, Escape
        # ditelan composer, maka pakai tombol Close (X) composer pada percobaan berikutnya.
        if attempt < 2:
            await page.keyboard.press("Escape")
        else:
            close = await _visible(page.get_by_role("button", name="Close", exact=True))
            if close is None:
                continue
            await close.click()
        try:
            await confirmation.wait_for(state="visible", timeout=1500)
        except PlaywrightError:
            continue
        await confirmation.click()
        await confirmation.wait_for(state="hidden")
        if await _visible(page.get_by_role("dialog")) is None:
            return
    raise DraftValidationError("Draft belum terkonfirmasi dibuang; browser akan ditutup tanpa Share.")
