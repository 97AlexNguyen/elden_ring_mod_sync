#!/usr/bin/env python3
"""Windows launcher that syncs this repository's _mod_data into Elden Ring's Game folder.

The repository is cached in the current user's LocalAppData directory.  Git is used
only for the cache; the game directory never receives a .git folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Elden Ring Mod Sync"
REPOSITORY_URL = "https://github.com/97AlexNguyen/elden_ring_mod_sync.git"
BRANCH = "main"
SOURCE_DIRECTORY = "_mod_data"
STATE_FILE_NAME = ".elden_ring_mod_sync.json"
LAUNCH_BAT_NAME = "launchmod_eldenring.bat"
SETTINGS_FILE_NAME = "settings.json"


class SyncError(RuntimeError):
    pass


def app_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise SyncError("Khong tim thay bien moi truong LOCALAPPDATA.")
    return Path(local_app_data) / "EldenRingModSync"


def cache_directory() -> Path:
    return app_data_directory() / "repository"


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


def find_git() -> str:
    git = shutil.which("git")
    if git:
        return git
    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Git" / "cmd" / "git.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    raise SyncError(
        "Can cai Git for Windows truoc khi dong bo. "
        "Tai tai: https://git-scm.com/download/win"
    )


def run_git(git: str, args: list[str], cwd: Path | None = None) -> None:
    command = [git, *args]
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SyncError(f"Git command that bai: {' '.join(args)}\n{detail}")


def update_cache(reporter: "ProgressReporter") -> Path:
    git = find_git()
    cache = cache_directory()
    cache.parent.mkdir(parents=True, exist_ok=True)

    reporter.start_indeterminate()
    try:
        if not (cache / ".git").is_dir():
            if cache.exists():
                raise SyncError(f"Cache khong hop le: {cache}")
            reporter.set_status("Dang tai du lieu mod lan dau...")
            run_git(git, ["clone", "--filter=blob:none", "--no-checkout", REPOSITORY_URL, str(cache)])
            run_git(git, ["sparse-checkout", "set", "--cone", SOURCE_DIRECTORY], cache)
        else:
            reporter.set_status("Dang kiem tra ban cap nhat...")

        run_git(git, ["fetch", "--prune", "origin", BRANCH], cache)
        run_git(git, ["reset", "--hard", f"origin/{BRANCH}"], cache)
        run_git(git, ["clean", "-ffd"], cache)
    finally:
        reporter.stop()

    source = cache / SOURCE_DIRECTORY
    if not source.is_dir():
        raise SyncError(f"Repo khong co thu muc {SOURCE_DIRECTORY}.")
    return source


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            # This file is for source-repo metadata, never for the game folder.
            if relative == "manifest.json":
                continue
            result[relative] = sha256(path)
    return result


def load_previous_state(game_directory: Path) -> dict[str, str]:
    state_path = game_directory / STATE_FILE_NAME
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("repository") != REPOSITORY_URL:
            return {}
        files = state.get("files", {})
        return files if isinstance(files, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def safe_target(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise SyncError(f"Duong dan khong an toan: {relative}")
    return target


def remove_empty_parents(path: Path, stop_at: Path) -> None:
    current = path.parent
    while current != stop_at:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def mirror_to_game(source: Path, game_directory: Path, reporter: "ProgressReporter") -> tuple[int, int]:
    reporter.set_status("Dang kiem tra file mod...")
    reporter.start_indeterminate()
    desired = relative_files(source)
    previous = load_previous_state(game_directory)

    to_remove = [relative for relative in previous if relative not in desired]
    to_copy = []
    for relative, source_hash in desired.items():
        target = safe_target(game_directory, relative)
        if target.is_file() and sha256(target) == source_hash:
            continue
        to_copy.append(relative)

    total = len(to_remove) + len(to_copy)
    reporter.set_determinate(total)

    copied = 0
    removed = 0

    reporter.set_status(f"Dang dong bo file... (0/{total})")
    for relative in to_remove:
        target = safe_target(game_directory, relative)
        if target.is_file() or target.is_symlink():
            target.unlink()
            remove_empty_parents(target, game_directory)
            removed += 1
        reporter.step()
        reporter.set_status(f"Dang dong bo file... ({copied + removed}/{total})")

    for relative in to_copy:
        target = safe_target(game_directory, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".modsync.tmp")
        shutil.copy2(source / relative, temporary)
        os.replace(temporary, target)
        copied += 1
        reporter.step()
        reporter.set_status(f"Dang dong bo file... ({copied + removed}/{total})")

    state = {
        "repository": REPOSITORY_URL,
        "branch": BRANCH,
        "files": desired,
    }
    (game_directory / STATE_FILE_NAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reporter.set_status(f"Da cap nhat {copied} file, da xoa {removed} file cu.")
    return copied, removed


def launch_game(game_directory: Path) -> None:
    bat = game_directory / LAUNCH_BAT_NAME
    if not bat.is_file():
        raise SyncError(f"Khong tim thay {LAUNCH_BAT_NAME} trong thu muc game.")
    # Use "start" via a shell so the batch file gets its own console and keeps
    # running independently of this launcher (mirrors double-clicking it).
    subprocess.Popen(
        f'start "" "{bat.name}"',
        cwd=str(game_directory),
        shell=True,
    )


class ProgressReporter:
    """Thread-safe helper that marshals progress-bar / status updates onto the Tk thread."""

    def __init__(self, root: tk.Tk, bar: ttk.Progressbar, status_var: tk.StringVar) -> None:
        self.root = root
        self.bar = bar
        self.status_var = status_var

    def set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def start_indeterminate(self) -> None:
        def _apply() -> None:
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)

        self.root.after(0, _apply)

    def stop(self) -> None:
        def _apply() -> None:
            self.bar.stop()

        self.root.after(0, _apply)

    def set_determinate(self, maximum: int) -> None:
        def _apply() -> None:
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=max(maximum, 1))
            self.bar["value"] = 0

        self.root.after(0, _apply)

    def step(self, amount: int = 1) -> None:
        def _apply() -> None:
            self.bar["value"] += amount

        self.root.after(0, _apply)

    def reset(self) -> None:
        def _apply() -> None:
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=100)
            self.bar["value"] = 0

        self.root.after(0, _apply)


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.resizable(False, False)

        self.settings = load_settings()
        self.game_directory = tk.StringVar(value=self.settings.get("game_directory", ""))
        self._suspend_save = False

        self._build_ui()
        self.reporter = ProgressReporter(self, self.progress, self.status)

        self.game_directory.trace_add("write", self._on_game_directory_changed)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Chon thu muc Game (chua eldenring.exe):").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(frame, width=58, textvariable=self.game_directory).grid(row=1, column=0, pady=(6, 10), sticky="ew")
        ttk.Button(frame, text="Chon...", command=self.choose_game_directory).grid(row=1, column=1, padx=(8, 0), pady=(6, 10))

        self.action_button = ttk.Button(frame, text="Dong bo & Choi game", command=self.start_sync_and_play)
        self.action_button.grid(row=2, column=0, columnspan=2, sticky="ew", ipady=4)

        self.progress = ttk.Progressbar(frame, orient="horizontal", mode="determinate", maximum=100)
        self.progress.grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="ew")

        self.status = tk.StringVar(value="San sang.")
        ttk.Label(frame, textvariable=self.status, wraplength=450).grid(row=4, column=0, columnspan=2, pady=(8, 0), sticky="w")

    def _on_game_directory_changed(self, *_) -> None:
        if self._suspend_save:
            return
        self.settings["game_directory"] = self.game_directory.get().strip()
        save_settings(self.settings)

    def choose_game_directory(self) -> None:
        selected = filedialog.askdirectory(title="Chon thu muc Game cua Elden Ring")
        if selected:
            self.game_directory.set(selected)

    def start_sync_and_play(self) -> None:
        selected = Path(self.game_directory.get().strip()).expanduser()
        if not self.game_directory.get().strip() or not selected.is_dir():
            messagebox.showerror(APP_NAME, "Hay chon mot thu muc Game hop le.")
            return
        if not (selected / "eldenring.exe").is_file():
            if not messagebox.askyesno(
                APP_NAME,
                "Khong thay eldenring.exe. Van dung thu muc nay?\n\n"
                "Thuong duong dan la ...\\ELDEN RING\\Game.",
            ):
                return

        self.action_button.configure(state="disabled")
        self.status.set("Dang dong bo...")
        threading.Thread(target=self._sync_and_play_worker, args=(selected,), daemon=True).start()

    def _sync_and_play_worker(self, game_directory: Path) -> None:
        try:
            source = update_cache(self.reporter)
            copied, removed = mirror_to_game(source, game_directory, self.reporter)
            self.reporter.set_status(
                f"Dong bo xong: {copied} cap nhat, {removed} file cu da xoa. Dang khoi dong game..."
            )
            launch_game(game_directory)
        except (SyncError, OSError) as error:
            self.after(0, lambda: messagebox.showerror(APP_NAME, str(error)))
            self.reporter.set_status("That bai.")
        else:
            self.reporter.set_status("Da dong bo va khoi dong game.")
        finally:
            self.reporter.reset()
            self.after(0, lambda: self.action_button.configure(state="normal"))


if __name__ == "__main__":
    Launcher().mainloop()
