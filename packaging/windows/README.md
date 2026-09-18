# Windows launcher and updates

Download `StocksHighIV-Windows.zip` from GitHub Releases, extract it, and open `StocksHighIV.exe`. Requires Windows 10/11 x64 and Python 3.11+ from python.org (enable Add Python to PATH). The launcher opens the local dashboard. Keep its window open; use **Check for updates** or **Install version …** there. It also checks automatically on startup and daily while running.

The first launch installs the app under `%LOCALAPPDATA%\StocksHighIV\versions`. Watchlists, schedules, market data, and reports live in the separate `data` and `output` folders under `%LOCALAPPDATA%\StocksHighIV`. Old downloaded launchers forward to the newest successfully installed version. Source ZIP / `.bat` users must install this packaged launcher once to acquire the updater; their old source-folder data is not automatically imported.

Updates come from the latest GitHub release's `windows-update.json`. The launcher checks the expected repository URL and version, verifies SHA-256 and the pinned Ed25519 public key before extraction, rejects unsafe ZIP entries, and checks the signed package's version. It retains the previous version, waits for any active market download/setup to finish, blocks new scans during replacement, and starts the new version. Cancellation before installation leaves the running app unchanged. A startup failure restores the previous version pointer; launch the previous downloaded launcher again if needed.

The update signing key is kept on the Mac build machine in Keychain (`StocksHighIV-updates`). The Windows executable is **not Authenticode-signed**; SmartScreen may warn on first use. Ed25519 update verification does not remove that Windows prompt.

## Build and publish

1. Increment the version/build in `packaging/macos/release.json`, shared by both platforms.
2. Commit and push. Run the **Windows package** GitHub Actions workflow. It builds on Windows, runs regressions, launches the generated EXE in native GUI smoke-test mode (including its single-instance lock), and uploads `Windows-package`.
3. Download that artifact into `dist/windows`. Build/notarize the matching Mac release using `packaging/macos/build.py`.
4. On the signing Mac, run `python3 packaging/windows/sign.py`. The private key stays in Keychain; only the archive signature and checksum go into `windows-update.json`.
5. After committing and pushing, run `python3 packaging/macos/publish.py`. It verifies both platform signatures and publishes a draft only after all four assets have uploaded: both ZIPs, `appcast.xml`, and `windows-update.json`.

Do not modify assets after signing. Publishing a latest release without either update feed breaks checks for that platform; the publisher requires all four assets. Private signing keys are never uploaded to GitHub Actions.

To build on your own Windows machine: `python -m pip install -r requirements.txt pyinstaller==6.22.3 cryptography==50.0.1`, then `python packaging/windows/build.py`.
