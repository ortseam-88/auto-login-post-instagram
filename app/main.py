# create by Ortseam
"""Mesin Instagram: login atau publish job bermedia per akun."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import logging
import math
import os
import re
from pathlib import Path
import sys
import signal
from datetime import datetime, timezone

import config
from accounts import Account, load_accounts

_ACCOUNT_TERMINAL_STATUSES = frozenset({
    "verification_required", "rate_limited", "account_disabled", "invalid_credentials",
    "profile_busy", "unexpected_page", "timeout",
})


class Dashboard:
    def __init__(self, workers: int):
        self.states = {index: ("-", "menunggu") for index in range(1, workers + 1)}
        self.finished = 0
        self.total = 0
        self.live = None

    def render(self):
        from rich.table import Table
        table = Table(title=f"ORTSEAM INSTAGRAM | selesai {self.finished}/{self.total}")
        table.add_column("Worker")
        table.add_column("Akun")
        table.add_column("Tahap")
        for worker, (account, stage) in self.states.items():
            table.add_row(str(worker), account, stage)
        return table

    def update(self, worker, account, stage):
        self.states[worker] = (account, stage)
        if self.live:
            self.live.update(self.render())

    def __enter__(self):
        if sys.stderr.isatty():
            try:
                from rich.live import Live
                from rich.console import Console
                self.live = Live(self.render(), console=Console(stderr=True), refresh_per_second=4)
                self.live.start()
            except ImportError:
                self.live = None
        return self

    def __exit__(self, *args):
        if self.live:
            self.live.stop()


def _seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Harus angka nonnegatif.") from None
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("Harus angka nonnegatif terbatas.")
    return result


def _workers(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Worker harus bilangan bulat.") from None
    if not 1 <= count <= 10:
        raise argparse.ArgumentTypeError("Worker harus 1 sampai 10; default 5.")
    return count


def _parser():
    parser = argparse.ArgumentParser(description="Mesin Instagram, satu profil per akun. Tidak ada Google/SERP/VPN.")
    parser.add_argument("--accounts", default=str(config.INPUT_ACCOUNTS_FILE), help="Input identifier|password; relatif folder instagram-program.")
    parser.add_argument("--account", action="append", default=[], help="Batasi ke identifier pilihan (bisa diulang, case-insensitive).")
    parser.add_argument("--jobs", help="Manifest JSON versi1; wajib untuk publish.")
    parser.add_argument("--job", action="append", default=[], metavar="ID", help="Batasi ke job_id tertentu (bisa diulang); hanya untuk publish.")
    parser.add_argument("--mode", choices=("login", "publish"), default="login", help="login: simpan session; publish: benar-benar Share.")
    parser.add_argument("--workers", type=_workers, default=config.DEFAULT_WORKERS, help="Slot akun berbeda, default 5 (maks 10); job akun sama selalu berurutan.")
    parser.add_argument("--manual-timeout", type=_seconds, default=config.MANUAL_TIMEOUT_SECS)
    parser.add_argument("--headless", action="store_true", help="Tanpa jendela; checkpoint langsung dilaporkan perlu tindakan manual.")
    parser.add_argument("--check", action="store_true", help="Validasi akun/job/media tanpa membuka browser.")
    parser.add_argument("--inventory-ready-file", help=argparse.SUPPRESS)
    return parser


async def _run(args, accounts: list[Account], jobs, passwords: list[str]) -> int:
    from playwright.async_api import Error as BrowserError
    from browser_session import open_session, profile_directory, ProfileBusyError
    from login_flow import login
    from models import PublishResult, UnsupportedOption, DraftValidationError
    from jobs import digest_job
    from results import RunStore
    from publisher import prepare, publish, discard

    log = logging.getLogger("instagram")
    config.chrome_path()
    grouped = defaultdict(list)
    for job in jobs:
        grouped[job.account.casefold()].append(job)
    units = [(account, grouped[account.identifier.casefold()]) for account in accounts if args.mode == "login" or account.identifier.casefold() in grouped]
    rows = []
    queue = asyncio.Queue()
    for unit in units:
        queue.put_nowait(unit)
    workers = min(args.workers, len(units))
    with RunStore(passwords) as store, Dashboard(workers) as dashboard:
        dashboard.total = len(jobs) if args.mode != "login" else len(accounts)

        async def one(account: Account, job, worker: int):
            identity = job.job_id if job else None
            row = {"account": account.identifier, "job_id": identity, "mode": args.mode,
                   "profile_dir": str(profile_directory(account.identifier)), "timestamp": datetime.now(timezone.utc).isoformat(),
                   "status": "starting", "detail": "", "cleanup": "pending"}
            submitted = False
            result = None
            try:
                digest = digest_job(job) if job else None
            except (OSError, ValueError):
                row.update(status="media_unavailable", detail="Media tidak dapat dibaca untuk SHA256; job tidak dijalankan.", cleanup="not_opened")
                rows.append(row)
                store.write_summary(rows)
                dashboard.finished += 1
                dashboard.update(worker, account.identifier, row["status"])
                return row
            if job and args.mode == "publish":
                try:
                    store.allow(job.job_id, digest)
                except ValueError:
                    row.update(status="blocked_by_ledger", detail="ID/payload job sudah terbit, ambigu, atau berubah. Periksa ledger; tidak submit ulang.", cleanup="not_opened")
                    rows.append(row)
                    store.write_summary(rows)
                    dashboard.finished += 1
                    dashboard.update(worker, account.identifier, row["status"])
                    return row

            def before_share():
                nonlocal submitted
                # File yang berubah selama pengisian draft tidak boleh memakai digest lama.
                if digest_job(job) != digest:
                    raise DraftValidationError("Media berubah selama persiapan; Share dibatalkan.")
                store.mark_submitting(job.job_id, digest, account.identifier)
                submitted = True
                dashboard.update(worker, account.identifier, "Share → menunggu bukti")

            try:
                dashboard.update(worker, account.identifier, "membuka session")
                async with open_session(account.identifier, headless=args.headless, worker_index=worker) as session:
                    login_result = await login(session.page, session.context, account,
                                               manual_timeout=0 if args.headless else args.manual_timeout,
                                               profile_dir=session.profile_dir)
                    row["reused"] = login_result.reused
                    if login_result.status != "success":
                        result = PublishResult(login_result.status, login_result.detail)
                    elif job is None:
                        result = PublishResult("success", login_result.detail)
                    else:
                        try:
                            dashboard.update(worker, account.identifier, f"draft {job.job_id}")
                            await prepare(session.page, job)
                            result = await publish(session.page, job, before_share)
                            store.finish(job.job_id, digest, account.identifier, result)
                        except (KeyboardInterrupt, asyncio.CancelledError):
                            # Browser teardown memegang cleanup; jangan biarkan
                            # kegagalan Discard menutupi pembatalan pengguna.
                            raise
                        except Exception:
                            if not submitted:
                                try:
                                    await discard(session.page)
                                except Exception:
                                    pass
                            raise
                    if result:
                        row.update(status=result.status, detail=result.detail, permalink=result.permalink)
                row["cleanup"] = "closed"
            except (KeyboardInterrupt, asyncio.CancelledError):
                try:
                    if submitted and (result is None or result.status != "published"):
                        result = PublishResult("unknown_after_submit", "Proses dibatalkan sesudah intent Share; periksa profil, jangan retry.")
                        store.finish(job.job_id, digest, account.identifier, result)
                        row.update(status=result.status, detail=result.detail)
                    elif result and result.status == "published":
                        row.update(status=result.status, detail=result.detail, permalink=result.permalink)
                    else:
                        row.update(status="interrupted", detail="Dibatalkan sebelum publikasi terkonfirmasi.")
                    row["cleanup"] = "interrupted"
                    rows.append(row)
                    store.write_summary(rows)
                except (OSError, ValueError):
                    # Ledger intent sebelumnya tetap memblokir resubmit. Error
                    # storage tidak boleh mengubah cancel menjadi lanjut akun.
                    log.error("Gagal menyimpan hasil pembatalan; jangan ulang job yang mungkin sudah Share.")
                raise
            except ProfileBusyError:
                row.update(status="profile_busy", detail="Profil akun sedang dipakai proses lain.", cleanup="not_opened")
            except (UnsupportedOption, DraftValidationError) as exc:
                row.update(status="unsupported_option" if isinstance(exc, UnsupportedOption) else "draft_invalid", detail=str(exc), cleanup="closed_or_failed")
            except (BrowserError, OSError, ValueError, TimeoutError):
                if result and (result.status in {"success", "published"} or result.status in _ACCOUNT_TERMINAL_STATUSES):
                    row.update(status=result.status, detail=result.detail, permalink=result.permalink, cleanup="failed")
                elif submitted:
                    result = PublishResult("unknown_after_submit", "Gangguan setelah intent Share; periksa profil sebelum rekonsiliasi.")
                    store.finish(job.job_id, digest, account.identifier, result)
                    row.update(status=result.status, detail=result.detail, cleanup="failed")
                else:
                    row.update(status="browser_error", detail="Browser/storage gagal sebelum Share. Tidak ada publikasi terkonfirmasi.", cleanup="failed")
            rows.append(row)
            store.write_summary(rows)
            dashboard.finished += 1
            dashboard.update(worker, account.identifier, row["status"])
            log.info("Akun=%s job=%s status=%s cleanup=%s", account.identifier, identity or "login", row["status"], row["cleanup"])
            return row

        async def worker(index):
            while not queue.empty():
                account, account_jobs = queue.get_nowait()
                try:
                    selected = account_jobs if args.mode != "login" else [None]
                    for offset, job in enumerate(selected):
                        completed = await one(account, job, index)
                        if completed["status"] in _ACCOUNT_TERMINAL_STATUSES:
                            # Sisa job tidak hilang dari ringkasan dan tidak
                            # mengulang autentikasi akun yang memerlukan bantuan.
                            for remaining in selected[offset + 1:]:
                                rows.append({"account": account.identifier, "job_id": remaining.job_id,
                                             "mode": args.mode, "status": "blocked_by_account", "cleanup": "not_opened",
                                             "detail": "Job sebelumnya pada akun ini gagal autentikasi/verifikasi."})
                                dashboard.finished += 1
                            store.write_summary(rows)
                            break
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(worker(index + 1)) for index in range(workers)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    expected = {"login": "success", "publish": "published"}[args.mode]
    return 0 if rows and all(row["status"] == expected and row["cleanup"] == "closed" for row in rows) else 1


def main(argv=None) -> int:
    from utils import configure_logging
    log = None
    previous_break = None
    stop_requested = False
    active_loop = active_task = None

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        if stop_requested:
            return
        stop_requested = True
        if active_loop is not None and active_task is not None:
            active_loop.call_soon_threadsafe(active_task.cancel)
        else:
            raise KeyboardInterrupt

    async def run_interruptible(args, accounts, jobs, passwords):
        nonlocal active_loop, active_task
        active_loop = asyncio.get_running_loop()
        active_task = asyncio.current_task()
        return await _run(args, accounts, jobs, passwords)

    try:
        if os.name == "nt":
            # CTRL_BREAK_EVENT bukan SIGINT: batalkan task utama agar finally
            # tetap menutup browser. Stop berulang tidak memotong cleanup.
            previous_break = signal.signal(signal.SIGBREAK, request_stop)
        args = _parser().parse_args(argv)
        if args.inventory_ready_file is not None:
            ready_file = Path(args.inventory_ready_file)
            if (not args.inventory_ready_file or not ready_file.is_absolute()
                    or ready_file.parent != config.OUTPUT_DIR
                    or ready_file.parent.resolve() != config.OUTPUT_DIR.resolve()
                    or not re.fullmatch(r"\.instagram-inventory-ready-[0-9a-f]{32}\.signal", ready_file.name)):
                raise ValueError("Path penanda inventaris tidak valid.")
        from results import account_inventory_lock
        with account_inventory_lock():
            if args.inventory_ready_file is not None:
                with os.fdopen(os.open(ready_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as marker:
                    marker.write(b"ready\n")
            all_accounts = load_accounts(config.resolve_path(args.accounts))
            if not all_accounts:
                raise ValueError("File akun kosong.")
            passwords = [account.password for account in all_accounts]
            log = configure_logging(passwords)
            accounts = all_accounts
            if args.account:
                selected = [value.strip().casefold() for value in args.account]
                if not all(selected) or len(selected) != len(set(selected)):
                    raise ValueError("Pilihan --account kosong atau duplikat.")
                identities = {account.identifier.casefold(): account for account in all_accounts}
                if any(value not in identities for value in selected):
                    raise ValueError("Identifier --account tidak terdapat pada input.")
                accounts = [identities[value] for value in selected]
            from jobs import load_jobs
            jobs = load_jobs(config.resolve_path(args.jobs), all_accounts) if args.jobs else []
            if args.mode != "login" and not jobs:
                raise ValueError("Mode publish memerlukan --jobs berisi media.")
            if args.account:
                selected_keys = {account.identifier.casefold() for account in accounts}
                jobs = [job for job in jobs if job.account.casefold() in selected_keys]
            if args.job:
                if args.mode == "login":
                    raise ValueError("Filter --job hanya berlaku untuk mode publish.")
                wanted = set(args.job)
                found = {job.job_id for job in jobs}
                missing = sorted(wanted - found)
                if missing:
                    raise ValueError("Job yang diminta tidak ada di manifest untuk akun terpilih: " + ", ".join(missing))
                jobs = [job for job in jobs if job.job_id in wanted]
            if args.mode != "login" and not jobs:
                raise ValueError("Tidak ada job untuk akun terpilih.")
            if args.check:
                log.info("VALID: %d akun terpilih, %d job bermedia; browser tidak dibuka.", len(accounts), len(jobs))
                return 0
            return asyncio.run(run_interruptible(args, accounts, jobs, passwords))
    except (KeyboardInterrupt, asyncio.CancelledError):
        if log:
            log.warning("Dibatalkan pengguna; tidak memulai akun berikutnya.")
        return 130
    except (ValueError, OSError, ImportError) as exc:
        if log:
            log.error("Konfigurasi/job tidak valid: %s", str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        else:
            sys.stderr.write("Input/dependensi tidak siap; periksa akun, path dan requirements.txt.\n")
        return 2
    except Exception:
        if log:
            log.error("Kesalahan internal; detail sensitif tidak dicetak. Periksa hasil/ledger sebelum mengulang.")
        return 1
    finally:
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)


if __name__ == "__main__":
    raise SystemExit(main())
