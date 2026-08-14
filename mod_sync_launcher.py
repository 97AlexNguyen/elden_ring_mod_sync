#!/usr/bin/env python3
"""Windows launcher that syncs this repository's _mod_data into Elden Ring's Game folder.

The repository is cached in the current user's LocalAppData directory.  Git is used
only for the cache; the game directory never receives a .git folder.

If the machine has no Git, a portable MinGit build is downloaded into the same
LocalAppData folder.  Nothing is installed system-wide and no elevation is needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Elden Ring Mod Sync"
REPOSITORY_URL = "https://github.com/97AlexNguyen/elden_ring_mod_sync.git"
BRANCH = "main"
SOURCE_DIRECTORY = "_mod_data"
STATE_FILE_NAME = ".elden_ring_mod_sync.json"

# Where each top-level folder inside _mod_data lands inside the Game directory.
# Folders that are not listed keep their own name, so _mod_data/mod/x becomes
# Game/mod/x and _mod_data/SeamlessCoop/x becomes Game/SeamlessCoop/x.  "config"
# is a staging folder: its contents belong loose in the Game root, next to
# eldenring.exe, which is how extra DLLs and launcher settings get shipped.
DIRECTORY_MAP = {"config": ""}
LAUNCH_BAT_NAME = "launchmod_eldenring.bat"
SETTINGS_FILE_NAME = "settings.json"
HASH_CACHE_FILE_NAME = "source_hashes.json"
TEMPORARY_SUFFIX = ".modsync.tmp"

# "sparse-checkout --cone" landed in 2.25 and "--filter=blob:none" in 2.19, so a
# Git older than this is skipped in favour of the portable build below.  Without
# the check an ancient Git on PATH fails deep inside the clone with a message
# nobody can act on.
MINIMUM_GIT_VERSION = (2, 25)

# Portable Git, published by the Git for Windows project for exactly this use.
# The hash is verified before anything is extracted, because the archive ships
# executables that this launcher then runs.
MINGIT_URL = (
    "https://github.com/git-for-windows/git/releases/download/"
    "v2.55.0.windows.3/MinGit-2.55.0.3-64-bit.zip"
)
MINGIT_SHA256 = "f48e2d2dc74a24454adc6d8fd0ac25bf9c2386f19cfb06202b9465aaad4f9f05"

# Keeps child processes from flashing a console window when this script is
# frozen into a windowed executable.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MEGABYTE = 1024 * 1024


class SyncError(RuntimeError):
    pass


class CacheCorrupted(SyncError):
    """Raised when the local cache is damaged and only a fresh clone can fix it."""


def app_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise SyncError("Khong tim thay bien moi truong LOCALAPPDATA.")
    return Path(local_app_data) / "EldenRingModSync"


def cache_directory() -> Path:
    return app_data_directory() / "repository"


def portable_git_directory() -> Path:
    return app_data_directory() / "git"


def portable_git_executable() -> Path:
    return portable_git_directory() / "cmd" / "git.exe"


def settings_path() -> Path:
    return app_data_directory() / SETTINGS_FILE_NAME


def load_settings() -> dict:
    try:
        path = settings_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, SyncError):
        pass
    return {}


def save_settings(settings: dict) -> None:
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, SyncError):
        pass


def hash_cache_path() -> Path:
    return app_data_directory() / HASH_CACHE_FILE_NAME


def load_hash_cache() -> dict:
    try:
        path = hash_cache_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, SyncError):
        pass
    return {}


def save_hash_cache(cache: dict) -> None:
    try:
        path = hash_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache), encoding="utf-8")
    except (OSError, SyncError):
        pass


def file_signature(path: Path) -> tuple[int, int]:
    """Size and modification time, used to skip re-reading an untouched file."""
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def force_remove_tree(path: Path) -> None:
    """Delete a tree even when it holds read-only files, as Git object stores do."""
    if not path.exists():
        return
    for child in path.rglob("*"):
        try:
            child.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(MEGABYTE), b""):
            digest.update(block)
    return digest.hexdigest()


def download_file(url: str, destination: Path, label: str, reporter: "ProgressReporter") -> None:
    request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    try:
        with urllib.request.urlopen(request) as response, destination.open("wb") as file:
            total = int(response.headers.get("Content-Length") or 0)
            if total:
                reporter.set_determinate(total)
            else:
                reporter.start_indeterminate()
            done = 0
            shown = -1
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                file.write(block)
                done += len(block)
                if total and done // MEGABYTE != shown:
                    shown = done // MEGABYTE
                    reporter.set_value(done)
                    reporter.set_status(f"{label} {shown}/{total // MEGABYTE} MB")
    except (urllib.error.URLError, OSError) as error:
        destination.unlink(missing_ok=True)
        raise SyncError(f"Khong tai duoc file:\n{url}\n\n{error}") from error
    finally:
        reporter.stop()


def install_portable_git(reporter: "ProgressReporter") -> None:
    root = app_data_directory()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "mingit.zip.tmp"
    staging = root / "git.new"

    try:
        reporter.set_status("Lan dau chay: dang tai Git (37 MB)...")
        download_file(MINGIT_URL, archive, "Dang tai Git...", reporter)

        reporter.set_status("Dang kiem tra file Git...")
        reporter.start_indeterminate()
        if sha256(archive) != MINGIT_SHA256:
            raise SyncError(
                "File Git tai ve khong dung ma kiem tra, da huy de an toan.\n"
                "Hay kiem tra ket noi mang roi thu lai."
            )

        reporter.set_status("Dang giai nen Git...")
        force_remove_tree(staging)
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(staging)
        except zipfile.BadZipFile as error:
            raise SyncError(f"Goi Git tai ve khong giai nen duoc:\n{error}") from error
        if not (staging / "cmd" / "git.exe").is_file():
            raise SyncError("Goi Git tai ve bi thieu git.exe.")

        # Only swap in the finished copy, so an interrupted install never leaves
        # a half-extracted Git behind.
        target = portable_git_directory()
        force_remove_tree(target)
        if target.exists():
            raise SyncError(
                f"Khong xoa duoc ban Git cu:\n{target}\n\n"
                "Hay dong cac chuong trinh dang dung thu muc nay roi thu lai."
            )
        staging.replace(target)
    finally:
        reporter.stop()
        archive.unlink(missing_ok=True)
        force_remove_tree(staging)


def run_git(git: str, args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        [git, *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        # Git for Windows speaks UTF-8, but the console codepage does not, and a
        # strict decode of an accented error message would blow up here instead
        # of surfacing the actual failure.
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SyncError(f"Git command that bai: {' '.join(args)}\n{detail}")
    return result.stdout.strip()


def git_version(git: str) -> tuple[int, ...] | None:
    """Parse "git version 2.55.0.windows.3" into (2, 55, 0), or None if unusable."""
    try:
        output = run_git(git, ["--version"])
    except (SyncError, OSError):
        return None
    parts = output.split()
    if len(parts) < 3:
        return None
    numbers = []
    for piece in parts[2].split("."):
        if not piece.isdigit():
            break
        numbers.append(int(piece))
    return tuple(numbers) or None


def ensure_git(reporter: "ProgressReporter") -> str:
    candidates = []
    system_git = shutil.which("git")
    if system_git:
        candidates.append(system_git)
    for program_files in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(program_files)
        if root:
            candidates.append(str(Path(root) / "Git" / "cmd" / "git.exe"))
    portable = portable_git_executable()
    candidates.append(str(portable))

    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        version = git_version(candidate)
        if version and version >= MINIMUM_GIT_VERSION:
            return candidate

    install_portable_git(reporter)
    if not portable.is_file():
        raise SyncError("Khong cai duoc Git di dong.")
    return str(portable)


def clone_cache(git: str, cache: Path, reporter: "ProgressReporter") -> None:
    reporter.set_status("Dang tai du lieu mod lan dau, co the mat vai phut...")
    reporter.start_indeterminate()
    try:
        run_git(git, ["clone", "--filter=blob:none", "--no-checkout", REPOSITORY_URL, str(cache)])
        run_git(git, ["sparse-checkout", "set", "--cone", SOURCE_DIRECTORY], cache)
    finally:
        reporter.stop()


def refresh_cache(git: str, cache: Path, reporter: "ProgressReporter") -> None:
    reporter.set_status("Dang kiem tra ban cap nhat...")
    reporter.start_indeterminate()
    try:
        # Cheap local sanity check.  A gutted or half-deleted .git is still a
        # directory, so without this its failure would surface from fetch below
        # and get misread as a network problem, leaving the user stuck.
        try:
            run_git(git, ["rev-parse", "--git-dir"], cache)
        except SyncError as error:
            raise CacheCorrupted(str(error)) from error

        # Network step.  A flaky connection must never cost the user their cache,
        # so this failure is reported as-is rather than triggering a rebuild.
        run_git(git, ["fetch", "--prune", "origin", BRANCH], cache)

        # Local steps.  Failing here means the working tree itself is damaged,
        # and a fresh clone is the only way out.
        try:
            run_git(git, ["reset", "--hard", f"origin/{BRANCH}"], cache)
            run_git(git, ["clean", "-ffd"], cache)
        except SyncError as error:
            raise CacheCorrupted(str(error)) from error
    finally:
        reporter.stop()


def normalized_remote(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git").lower()


def cache_points_at_repository(git: str, cache: Path) -> bool:
    """Whether the cache still tracks REPOSITORY_URL.

    A cache cloned from an older URL would otherwise keep fetching the old repo
    forever, silently shipping stale mods long after the constant changed.
    """
    try:
        origin = run_git(git, ["remote", "get-url", "origin"], cache)
    except SyncError:
        try:
            run_git(git, ["rev-parse", "--git-dir"], cache)
        except SyncError:
            # The repository itself is damaged rather than mis-pointed.  Say so
            # by leaving it to refresh_cache, which reports a corrupt cache.
            return True
        # A healthy repository with no usable origin can never fetch, so rebuild
        # it here instead of letting the network step take the blame.
        return False
    return normalized_remote(origin) == normalized_remote(REPOSITORY_URL)


def discard_cache(cache: Path) -> None:
    force_remove_tree(cache)
    if cache.exists():
        raise SyncError(
            f"Khong xoa duoc cache hong:\n{cache}\n\n"
            "Hay dong cac chuong trinh dang mo thu muc nay roi thu lai."
        )


def update_cache(reporter: "ProgressReporter") -> Path:
    git = ensure_git(reporter)
    cache = cache_directory()
    cache.parent.mkdir(parents=True, exist_ok=True)

    # A clone interrupted partway leaves a directory with no .git inside.  Treat
    # that as disposable instead of turning it into a permanent dead end.
    if cache.exists() and not (cache / ".git").is_dir():
        reporter.set_status("Cache khong hop le, dang tao lai...")
        discard_cache(cache)

    if (cache / ".git").is_dir() and not cache_points_at_repository(git, cache):
        reporter.set_status("Cache tro toi repo khac, dang tai lai...")
        discard_cache(cache)

    if (cache / ".git").is_dir():
        try:
            refresh_cache(git, cache, reporter)
        except CacheCorrupted:
            reporter.set_status("Cache bi hong, dang tai lai tu dau...")
            discard_cache(cache)
            clone_cache(git, cache, reporter)
            refresh_cache(git, cache, reporter)
    else:
        clone_cache(git, cache, reporter)
        refresh_cache(git, cache, reporter)

    source = cache / SOURCE_DIRECTORY
    if not source.is_dir():
        raise SyncError(f"Repo khong co thu muc {SOURCE_DIRECTORY}.")
    return source


def game_relative_path(source_relative: str) -> str:
    """Translate a path inside _mod_data into its path inside the Game folder."""
    head, separator, tail = source_relative.partition("/")
    if not separator or head not in DIRECTORY_MAP:
        return source_relative
    destination = DIRECTORY_MAP[head]
    return f"{destination}/{tail}" if destination else tail


def collect_files(root: Path) -> dict[str, tuple[str, str]]:
    """Map each destination inside Game to its source path and content hash.

    Hashes are memoised against size and mtime.  The payload runs to hundreds of
    megabytes and Git only rewrites the files that actually changed, so a plain
    re-read of everything would dominate the runtime of an otherwise no-op sync.
    """
    cached = load_hash_cache()
    fresh: dict[str, list] = {}
    result: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        source_relative = path.relative_to(root).as_posix()
        # This file is for source-repo metadata, never for the game folder.
        if source_relative == "manifest.json":
            continue
        destination = game_relative_path(source_relative)
        if destination in result:
            raise SyncError(
                "Hai file cung do vao mot cho trong thu muc game:\n"
                f"{result[destination][0]}\n{source_relative}\n-> {destination}"
            )
        size, mtime = file_signature(path)
        entry = cached.get(source_relative)
        if (
            isinstance(entry, list)
            and len(entry) == 3
            and entry[:2] == [size, mtime]
            and isinstance(entry[2], str)
        ):
            digest = entry[2]
        else:
            digest = sha256(path)
        fresh[source_relative] = [size, mtime, digest]
        result[destination] = (source_relative, digest)
    save_hash_cache(fresh)
    return result


def load_previous_state(game_directory: Path) -> dict[str, dict]:
    """Read the per-file record left by the previous sync.

    Entries used to be a bare hash string and are still accepted as one; they
    simply carry no stat to compare against, so those files get re-hashed once.
    """
    state_path = game_directory / STATE_FILE_NAME
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("repository") != REPOSITORY_URL:
            return {}
        files = state.get("files", {})
        if not isinstance(files, dict):
            return {}
    except (OSError, json.JSONDecodeError):
        return {}

    previous: dict[str, dict] = {}
    for relative, value in files.items():
        if isinstance(value, str):
            previous[relative] = {"sha256": value}
        elif isinstance(value, dict) and isinstance(value.get("sha256"), str):
            previous[relative] = value
    return previous


def safe_target(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise SyncError(f"Duong dan khong an toan: {relative}")
    return target


def remove_empty_parents(path: Path, stop_at: Path) -> None:
    """Prune directories left empty by a deletion, never climbing past stop_at.

    The containment test is what keeps this inside the game folder: if the two
    paths ever disagree on form, the loop refuses to run instead of walking up
    the drive removing whatever happens to be empty.
    """
    current = path.parent
    while current != stop_at and stop_at in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def locked_file_error(error: OSError) -> SyncError:
    return SyncError(
        f"Khong truy cap duoc file mod:\n{error}\n\n"
        "Hay dam bao Elden Ring dang khong chay roi thu lai."
    )


def target_is_current(target: Path, expected: str, recorded: dict | None) -> bool:
    """Whether the game already holds the wanted content.

    The recorded stat is trusted only when it matches the file on disk exactly;
    anything else falls back to reading the file, so a hand-edited or replaced
    file is still noticed.
    """
    if not target.is_file():
        return False
    if recorded and recorded.get("sha256") == expected:
        try:
            size, mtime = file_signature(target)
        except OSError:
            size = None
        if size is not None and recorded.get("size") == size and recorded.get("mtime_ns") == mtime:
            return True
    return sha256(target) == expected


def write_state(game_directory: Path, desired: dict[str, tuple[str, str]]) -> None:
    """Record what the game folder now holds.

    Keyed by destination, so a file that later moves inside _mod_data is still
    recognised and cleaned up correctly at its old location in the game folder.
    Each entry carries the stat of the file as written, which lets the next run
    skip re-hashing it.
    """
    files = {}
    for relative, (_, digest) in desired.items():
        entry = {"sha256": digest}
        try:
            size, mtime = file_signature(safe_target(game_directory, relative))
        except OSError:
            pass
        else:
            entry["size"] = size
            entry["mtime_ns"] = mtime
        files[relative] = entry

    state = {"repository": REPOSITORY_URL, "branch": BRANCH, "files": files}
    (game_directory / STATE_FILE_NAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def mirror_to_game(source: Path, game_directory: Path, reporter: "ProgressReporter") -> tuple[int, int]:
    reporter.set_status("Dang kiem tra file mod...")
    reporter.start_indeterminate()
    try:
        desired = collect_files(source)
        previous = load_previous_state(game_directory)

        stale = [relative for relative in previous if relative not in desired]
        outdated = []
        for relative, (_, source_hash) in desired.items():
            target = safe_target(game_directory, relative)
            if not target_is_current(target, source_hash, previous.get(relative)):
                outdated.append(relative)
    except PermissionError as error:
        # A file the game still holds open fails here, before a single write.
        raise locked_file_error(error) from error

    total = len(stale) + len(outdated)
    reporter.set_determinate(total)

    copied = 0
    removed = 0
    try:
        for relative in stale:
            target = safe_target(game_directory, relative)
            if target.is_file() or target.is_symlink():
                target.unlink()
                remove_empty_parents(target, game_directory)
                removed += 1
            reporter.step()
            reporter.set_status(f"Dang dong bo... {copied + removed}/{total}")

        for relative in outdated:
            source_relative, _ = desired[relative]
            target = safe_target(game_directory, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + TEMPORARY_SUFFIX)
            try:
                shutil.copy2(source / source_relative, temporary)
                os.replace(temporary, target)
            finally:
                # A copy that dies partway must not leave its scratch file in the
                # game folder: nothing downstream tracks it, so it would sit there
                # for good.  After a successful replace this is already gone.
                temporary.unlink(missing_ok=True)
            copied += 1
            reporter.step()
            reporter.set_status(f"Dang dong bo... {copied + removed}/{total}")
    except PermissionError as error:
        raise locked_file_error(error) from error

    # Written even when nothing changed.  A game folder that already matched the
    # repo would otherwise never get a state file, and every file later removed
    # from the repo would stay behind forever.
    write_state(game_directory, desired)
    if not total:
        reporter.set_status("Mod da la ban moi nhat.")
    return copied, removed


def launch_game(game_directory: Path) -> None:
    bat = game_directory / LAUNCH_BAT_NAME
    if not bat.is_file():
        raise SyncError(f"Khong tim thay {LAUNCH_BAT_NAME} trong thu muc game.")
    # "start" hands the batch file its own console and detaches it, so the game
    # keeps running after this launcher is closed.
    subprocess.Popen(
        ["cmd", "/c", "start", "", bat.name],
        cwd=str(game_directory),
        creationflags=CREATE_NO_WINDOW,
    )


class ProgressReporter:
    """Marshals progress-bar and status updates from the worker thread onto Tk."""

    def __init__(self, root: tk.Tk, bar: ttk.Progressbar, status_var: tk.StringVar) -> None:
        self.root = root
        self.bar = bar
        self.status_var = status_var

    def post(self, callback) -> None:
        """Hand a UI call to Tk, tolerating a window closed mid-sync.

        The worker is a daemon thread and keeps reporting for a moment after the
        window goes away; without this the thread would die on a TclError far
        from anything the user could see.
        """
        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def set_status(self, text: str) -> None:
        self.post(lambda: self.status_var.set(text))

    def start_indeterminate(self) -> None:
        def apply() -> None:
            self.bar.stop()
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)

        self.post(apply)

    def stop(self) -> None:
        self.post(self.bar.stop)

    def set_determinate(self, maximum: int) -> None:
        def apply() -> None:
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=max(maximum, 1))
            self.bar["value"] = 0

        self.post(apply)

    def set_value(self, value: int) -> None:
        self.post(lambda: self.bar.configure(value=value))

    def step(self, amount: int = 1) -> None:
        def apply() -> None:
            self.bar["value"] = self.bar["value"] + amount

        self.post(apply)

    def reset(self) -> None:
        def apply() -> None:
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=100)
            self.bar["value"] = 0

        self.post(apply)


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.resizable(False, False)

        self.settings = load_settings()
        self.game_directory = tk.StringVar(value=self.settings.get("game_directory", ""))
        self.busy = False
        self._save_job: str | None = None

        self._build_ui()
        self.reporter = ProgressReporter(self, self.progress, self.status)
        self.game_directory.trace_add("write", self._on_game_directory_changed)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Chon thu muc Game (chua eldenring.exe):").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(frame, width=58, textvariable=self.game_directory).grid(
            row=1, column=0, pady=(6, 10), sticky="ew"
        )
        ttk.Button(frame, text="Chon...", command=self.choose_game_directory).grid(
            row=1, column=1, padx=(8, 0), pady=(6, 10)
        )

        self.action_button = ttk.Button(
            frame, text="Dong bo & Choi game", command=self.start_sync_and_play
        )
        self.action_button.grid(row=2, column=0, columnspan=2, sticky="ew", ipady=4)

        self.progress = ttk.Progressbar(frame, orient="horizontal", mode="determinate", maximum=100)
        self.progress.grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="ew")

        self.status = tk.StringVar(value="San sang.")
        ttk.Label(frame, textvariable=self.status, wraplength=460).grid(
            row=4, column=0, columnspan=2, pady=(8, 0), sticky="w"
        )

    def _on_game_directory_changed(self, *_) -> None:
        # The entry fires this on every keystroke, so the write is debounced and
        # half-typed paths never reach the settings file.
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(600, self._save_game_directory)

    def _save_game_directory(self) -> None:
        self._save_job = None
        self.settings["game_directory"] = self.game_directory.get().strip()
        save_settings(self.settings)

    def _flush_settings(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_game_directory()

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            APP_NAME,
            "Dang dong bo. Thoat bay gio co the de lai mod chua day du.\n\nVan thoat?",
        ):
            return
        self._flush_settings()
        self.destroy()

    def post(self, callback) -> None:
        """Queue a UI call from the worker thread; see ProgressReporter.post."""
        try:
            self.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def choose_game_directory(self) -> None:
        selected = filedialog.askdirectory(title="Chon thu muc Game cua Elden Ring")
        if selected:
            self.game_directory.set(selected)

    def start_sync_and_play(self) -> None:
        if self.busy:
            return
        raw = self.game_directory.get().strip()
        # Resolved once, here: everything downstream compares against resolved
        # paths, and a folder reached through a junction or a "..\" would other-
        # wise never compare equal to them.
        try:
            selected = Path(raw).expanduser().resolve() if raw else None
        except OSError:
            selected = None
        if selected is None or not selected.is_dir():
            messagebox.showerror(APP_NAME, "Hay chon mot thu muc Game hop le.")
            return
        if not (selected / "eldenring.exe").is_file():
            if not messagebox.askyesno(
                APP_NAME,
                "Khong thay eldenring.exe. Van dung thu muc nay?\n\n"
                "Thuong duong dan la ...\\ELDEN RING\\Game.",
            ):
                return

        self._flush_settings()
        self.busy = True
        self.action_button.configure(state="disabled")
        self.status.set("Dang bat dau...")
        threading.Thread(target=self._worker, args=(selected,), daemon=True).start()

    def _worker(self, game_directory: Path) -> None:
        try:
            source = update_cache(self.reporter)
            copied, removed = mirror_to_game(source, game_directory, self.reporter)
            self.reporter.set_status(
                f"Da dong bo {copied} file, xoa {removed} file cu. Dang khoi dong game..."
            )
            launch_game(game_directory)
        # Deliberately broad: this dialog is the only place a worker failure can
        # ever be seen.  Anything narrower and an unforeseen error would leave
        # the button re-enabled with no explanation of what went wrong.
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, (SyncError, OSError))
                else f"Loi khong mong doi:\n{error.__class__.__name__}: {error}"
            )
            self.post(lambda: messagebox.showerror(APP_NAME, message))
            self.reporter.set_status("That bai.")
        else:
            self.reporter.set_status(
                f"Xong: {copied} file cap nhat, {removed} file cu da xoa. Da khoi dong game."
            )
        finally:
            self.reporter.reset()
            self.post(self._finish)

    def _finish(self) -> None:
        self.busy = False
        self.action_button.configure(state="normal")


if __name__ == "__main__":
    Launcher().mainloop()
