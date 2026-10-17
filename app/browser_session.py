# create by Ortseam
"""Sesi Chrome persisten per akun dengan kunci eksklusif antarproses."""

from __future__ import annotations

import asyncio
import errno
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProfileBusyError(RuntimeError):
    """Profil masih dipakai oleh proses lain; sesi kedua tidak dijalankan."""


@dataclass(frozen=True)
class BrowserSession:
    context: BrowserContext = field(repr=False)
    page: Page = field(repr=False)
    profile_dir: Path


def profile_directory(identifier: str) -> Path:
    normalized = identifier.strip().casefold().encode("utf-8")
    return config.session_root() / sha256(normalized).hexdigest()[:24]


@contextmanager
def _profile_lock(lock_path: Path) -> Iterator[None]:
    # Jangan hapus file ini: penghapusan bisa memisahkan kunci antarproses.
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ProfileBusyError("Profil akun sedang dipakai oleh proses lain.") from None
            raise

        try:
            # Windows mengizinkan penguncian melewati EOF. Inisialisasi setelah
            # terkunci agar proses kedua tidak membaca/menulis byte terkunci.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


async def _close_resources(context, playwright) -> BaseException | None:
    """Coba kedua tahap meski salah satunya gagal; hanya sumber daya milik sesi."""
    async def attempt(operation):
        try:
            await operation()
        except asyncio.CancelledError:
            # wait_for harus mengubah timeout internal menjadi TimeoutError.
            raise
        except BaseException as exc:
            return exc
        return None

    failure = None
    for resource, method in ((context, "close"), (playwright, "stop")):
        if resource is None:
            continue
        try:
            error = await asyncio.wait_for(
                attempt(getattr(resource, method)), timeout=config.CLOSE_TIMEOUT_SECS
            )
        except BaseException as exc:
            error = exc
        if error is not None and (
            failure is None or isinstance(error, (asyncio.CancelledError, KeyboardInterrupt))
        ):
            failure = error
    return failure


@asynccontextmanager
async def open_session(
    identifier: str, *, headless: bool = False, worker_index: int = 1
) -> AsyncIterator[BrowserSession]:
    if type(worker_index) is not int or not 1 <= worker_index <= config.GRID_COLUMNS * config.GRID_ROWS:
        raise ValueError("Slot worker berada di luar kapasitas grid browser.")
    row, column = divmod(worker_index - 1, config.GRID_COLUMNS)
    left = config.WINDOW_LEFT + column * (config.BROWSER_WIDTH + config.WINDOW_GAP)
    top = config.WINDOW_TOP + row * (config.BROWSER_HEIGHT + config.WINDOW_GAP)
    viewport_options = (
        {"viewport": config.HEADLESS_VIEWPORT, "device_scale_factor": config.BROWSER_SCALE_FACTOR}
        if headless else {"no_viewport": True}
    )
    profile_dir = profile_directory(identifier)
    profile_dir.parent.mkdir(parents=True, exist_ok=True)

    with _profile_lock(profile_dir.with_suffix(".lock")):
        profile_dir.mkdir(exist_ok=True)
        playwright = None
        context: BrowserContext | None = None
        primary: BaseException | None = None
        try:
            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                executable_path=config.chrome_path(),
                headless=headless,
                **viewport_options,
                locale="en-US",
                timezone_id="Asia/Jakarta",
                slow_mo=config.SLOW_MO,
                accept_downloads=False,
                args=[
                    "--no-default-browser-check",
                    f"--force-device-scale-factor={config.BROWSER_SCALE_FACTOR}",
                    "--high-dpi-support=1",
                    f"--window-size={config.BROWSER_WIDTH},{config.BROWSER_HEIGHT}",
                    f"--window-position={left},{top}",
                ],
            )
            context.set_default_navigation_timeout(config.TIMEOUT_NAVIGATE_MS)
            context.set_default_timeout(config.TIMEOUT_ELEMENT_MS)
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            yield BrowserSession(context=context, page=page, profile_dir=profile_dir)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            # shield mencegah Ctrl-C membatalkan close/stop di tengah jalan.
            # Tetap await task sampai selesai: tidak meninggalkan task cleanup.
            cleanup = asyncio.create_task(_close_resources(context, playwright))
            interrupted: BaseException | None = None
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except (asyncio.CancelledError, KeyboardInterrupt) as exc:
                    interrupted = exc
            failure = cleanup.result()
            if not isinstance(primary, (asyncio.CancelledError, KeyboardInterrupt)):
                if interrupted is not None:
                    raise interrupted
                if isinstance(failure, (asyncio.CancelledError, KeyboardInterrupt)):
                    raise failure
                if primary is None and failure is not None:
                    raise failure
