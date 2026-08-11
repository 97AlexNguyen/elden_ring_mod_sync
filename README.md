# Elden Ring Mod Sync

Windows launcher that mirrors this repository's `_mod_data` directory into an
Elden Ring `Game` directory. It keeps a private Git cache in
`%LOCALAPPDATA%\EldenRingModSync\repository`, so subsequent syncs download only
new Git objects instead of a new full ZIP package.

## For players

1. Download `EldenRingModSync.exe` from this repository's Releases page.
2. Run it and select the `Game` directory containing `eldenring.exe`, normally:
   `...\Steam\steamapps\common\ELDEN RING\Game`.
3. Click **Dong bo & Choi game**. It checks for updates, syncs the mod, and
   starts the game via `launchmod_eldenring.bat`.

Nothing else needs installing. The chosen game directory is remembered, so from
the second run onwards it is one click.

### First run details

- If the machine has no Git, the launcher downloads a portable
  [MinGit](https://github.com/git-for-windows/git/releases) build (37 MB) into
  `%LOCALAPPDATA%\EldenRingModSync\git`. Its SHA-256 is verified before use.
  Nothing is installed system-wide and no administrator prompt appears.
- Windows may show **"Windows protected your PC"**, because the executable is not
  code-signed. Choose *More info* then *Run anyway*.
- Close Elden Ring before syncing, otherwise the mod files cannot be replaced.

To remove everything the launcher created, delete
`%LOCALAPPDATA%\EldenRingModSync`.

## What gets written where

Folders inside `_mod_data` keep their own name inside `Game`, with one exception:
`config` is a staging folder whose contents are placed loose in the `Game` root.

| Repository | Game directory |
| --- | --- |
| `_mod_data/mod/regulation.bin` | `Game/mod/regulation.bin` |
| `_mod_data/SeamlessCoop/ersc_settings.ini` | `Game/SeamlessCoop/ersc_settings.ini` |
| `_mod_data/config/config_eldenring.toml` | `Game/config_eldenring.toml` |
| `_mod_data/config/dlls/extra.dll` | `Game/dlls/extra.dll` |

Use `config` for anything that has to sit next to `eldenring.exe`, such as
ModEngine2's `.toml` or extra DLLs. To add another mapping, edit `DIRECTORY_MAP`
at the top of `mod_sync_launcher.py`.

Only files previously recorded in `Game/.elden_ring_mod_sync.json` are removed
when they no longer exist in the repository. Files the launcher has never managed
are left untouched.

**These files are overwritten on every sync.** That is the point for shared
settings like `ersc_settings.ini`, but it does mean a player's local edits to any
managed file are reverted the next time they press the button. Keep anything
meant to stay per-player out of `_mod_data`.

Because `config` writes into the `Game` root, a mistake there overwrites real game
files. Never place `eldenring.exe`, or anything else shipped by the game, in it.

## For the mod maintainer

Put distributable files under `_mod_data`, then use normal Git workflow:

```powershell
git add _mod_data
git commit -m "Update mod"
git push
```

Do not put local backup/source files such as `.bak`, `.prev`, or editor exports in
`_mod_data` unless players need them: they will be copied into the game folder.

`.gitattributes` disables line-ending translation for everything under
`_mod_data`. Without it Git rewrites files it guesses are text, such as `.hks`
scripts and `.ini` files, and players end up with bytes that differ from what was
committed. Keep that rule in place.

For large binary assets that change frequently, consider Git LFS. Note that the
launcher clones with `--filter=blob:none`, so a plain repository already avoids
fetching historical versions of assets.

## Build the launcher

On a Windows machine with Python 3.10+:

```powershell
python -m pip install pyinstaller
.\build_exe.ps1
```

The output is `dist\EldenRingModSync.exe`. Publish that file as a GitHub Release
asset. The executable is intentionally separate from `_mod_data`; players download
it once and it updates the mod from the repository whenever they run it.
