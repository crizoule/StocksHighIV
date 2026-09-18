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
