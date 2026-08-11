# Elden Ring Mod Sync

Windows launcher that mirrors this repository's `_mod_data` directory into an
Elden Ring `Game` directory. It keeps a private Git cache in
`%LOCALAPPDATA%\EldenRingModSync\repository`, so subsequent syncs download only
new Git objects instead of a new full ZIP package.

## For players

1. Install [Git for Windows](https://git-scm.com/download/win) once.
2. Download `EldenRingModSync.exe` from this repository's Releases page.
3. Run it and select the `Game` directory containing `eldenring.exe`, normally:
   `...\Steam\steamapps\common\ELDEN RING\Game`.
4. Click **Dong bo mod** before starting the game.

The launcher installs the contents of `_mod_data` directly into `Game`; therefore
`_mod_data/mod/...` becomes `Game/mod/...`.

Only files previously recorded in `Game/.elden_ring_mod_sync.json` are removed
when they no longer exist in the repository. Files the launcher has never managed
are left untouched.

## For the mod maintainer

Put distributable files under `_mod_data`, then use normal Git workflow:

```powershell
git add _mod_data
git commit -m "Update mod"
git push
```

Do not put local backup/source files such as `.bak`, `.prev`, or editor exports in
`_mod_data` unless players need them: they will be copied into the game folder.

For large binary assets that change frequently, configure Git LFS before the first
public release.

## Build the launcher

On a Windows machine with Python 3.10+:

```powershell
.\build.ps1
```

The output is `dist\EldenRingModSync.exe`. Publish that file as a GitHub Release
asset. The executable is intentionally separate from `_mod_data`; players download
it once and it updates the mod from the repository whenever they run it.
