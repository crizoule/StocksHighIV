# Mac distribution

The native launcher opens the existing local browser dashboard, including setup and download progress. It requires Python 3.11+ from python.org or Homebrew. Intel and Apple Silicon are included in one app. Quit StocksHighIV from the Dock or its menu to stop the local server and download worker.

The app keeps its signed bundle unchanged. Code is copied to a versioned folder under `~/Library/Application Support/StocksHighIV/versions/`; downloaded data and reports are shared across app versions in that Application Support folder. Repository-launcher data is separate. Startup errors are written to `launcher.log` in the same folder.

## One-time developer setup

1. In Xcode → Settings → Accounts, sign in with your Apple Developer account and select the paid team.
2. Manage Certificates → + → **Developer ID Application**. An **Apple Development** certificate is not sufficient. Creating this certificate may require your team's Account Holder.
3. Create an app-specific password at account.apple.com if using Apple Account authentication for notarization. Run this command in your own Terminal and follow its interactive prompts for Apple Account, app-specific password and Team ID:

   ```sh
   xcrun notarytool store-credentials StocksHighIV-notary
   ```

   Credentials stay in your Keychain. Do not put passwords or private keys in this repository or chat. An existing notarytool Keychain profile can be used instead.

## Build and release

From the repository folder, find the distribution identity:

```sh
security find-identity -v -p codesigning
```

Then use its exact Developer ID Application name or SHA-1:

```sh
python3 packaging/macos/build.py --identity 'Developer ID Application: Your Name (TEAMID)' --notary-profile StocksHighIV-notary
```

The script compiles both architectures, signs with hardened runtime and secure timestamp, submits to Apple, staples the notarization ticket, checks Gatekeeper, and creates `dist/macos/StocksHighIV-macOS.zip`. Publish that ZIP as a GitHub Release asset; GitHub's automatic source ZIP does not contain the signed app. Users extract the app, optionally drag it to Applications, and double-click it. macOS can still display its normal first-open downloaded-app confirmation.

For local build verification only, omit both options:

```sh
python3 packaging/macos/build.py
```

This creates an explicitly **UNSIGNED** test ZIP with an ad-hoc signature; it does not remove Gatekeeper warnings and must not be presented as a notarized release. No account credentials are needed for this build.

## In-app updates (1.1.0+)

The Mac launcher embeds Sparkle 2.10.0 (download pinned by SHA-256), checks for updates daily, and offers **Check for Updates…** in its application menu. Installing requires the user's action. Sparkle verifies Ed25519 archive signatures and Apple code signatures; notarization is retained. The archive is verified before extraction. Its private update-signing key stays in the `StocksHighIV-updates` Keychain account; only the public key is in `release.json`.

When installation is requested, the launcher reserves the Python backend. Active setup/download work finishes first; new scheduled/manual scans are blocked until restart or cancellation. The browser status bar reports the pending update. Application Support data, watchlists, and schedules are outside the replaced bundle. Put the app in a writable Applications folder; macOS may request authorization when replacing a protected copy.

For every release:

1. Increase both `version` and numeric `build` in `release.json`. Never reuse a build number.
2. Run the signed build command above. It now generates both the notarized ZIP and a signed `dist/macos/appcast.xml` after verifying the signatures.
3. Publish **both** files as assets of the corresponding GitHub release (`v` plus the version). Draft the release, upload both assets, then publish it. Keep all releases' assets immutable; don't replace a ZIP without re-signing its feed.
4. The feed URL is `https://github.com/crizoule/StocksHighIV/releases/latest/download/appcast.xml`. Therefore every new latest release must include the appcast. Pushing source changes alone does not publish an application update.

Users on 1.0.x need one manual installation of 1.1.0 or later to acquire the updater. Windows/source launchers do not self-update. Preserve the update-signing Keychain key when moving build machines; losing it requires a deliberate key-rotation plan.

After committing and pushing, `python3 packaging/macos/publish.py` verifies both update signatures, uploads both assets to a draft release, and publishes it as latest. This avoids exposing a latest release before its update feed is available. The publisher refuses an uncommitted worktree or a mismatched archive/feed.
