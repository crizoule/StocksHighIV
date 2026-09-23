import Cocoa
import Sparkle
import CryptoKit

// The signed bundle stays immutable; Python and market data live in Application Support.
final class Launcher: NSObject, NSApplicationDelegate, SPUUpdaterDelegate {
    var process: Process?
    var log: FileHandle?
    var updaterController: SPUStandardUpdaterController!
    var backendIdentity = ""
    var pendingInstall: (() -> Void)?
    var updateTimer: Timer?

    @objc func checkForUpdates(_ sender: Any?) {
        updaterController.checkForUpdates(sender)
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                 untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        pendingInstall = installHandler
        reserveUpdate()
        return true
    }

    // Whether the running app is free to be replaced. Unreachable counts as free: there is nothing left to protect.
    enum Readiness { case ready, busy, unreachable }

    func updateBackend(_ action: String, completion: @escaping (Readiness) -> Void) {
        let base = "http://127.0.0.1:8932"
        var request = URLRequest(url: URL(string: base + "/api/status")!)
        request.timeoutInterval = 5
        URLSession.shared.dataTask(with: request) { data, _, _ in
            guard let data, let status = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  status["identity"] as? String == self.backendIdentity,
                  let token = status["token"] as? String else {
                DispatchQueue.main.async { completion(.unreachable) }; return
            }
            var post = URLRequest(url: URL(string: base + "/api/" + action)!)
            post.httpMethod = "POST"; post.timeoutInterval = 5; post.httpBody = Data("{}".utf8)
            post.setValue(base, forHTTPHeaderField: "Origin")
            post.setValue(token, forHTTPHeaderField: "X-App-Token")
            post.setValue("application/json", forHTTPHeaderField: "Content-Type")
            URLSession.shared.dataTask(with: post) { data, response, _ in
                let result = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    DispatchQueue.main.async { completion(.unreachable) }; return
                }
                DispatchQueue.main.async { completion(result?["ready"] as? Bool == true ? .ready : .busy) }
            }.resume()
        }.resume()
    }

    func reserveUpdate(afterAsking: Bool = false) {
        updateBackend("prepare-update") { state in
            guard let install = self.pendingInstall else {
                self.updateBackend("cancel-update") { _ in }; return
            }
            switch state {
            case .ready, .unreachable:  // idle, or no running app left to interrupt
                self.pendingInstall = nil
                install()
            case .busy where afterAsking:
                self.updateTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { _ in self.reserveUpdate(afterAsking: true) }
            case .busy:
                self.askAboutRunningDownload()  // otherwise the click looks like it did nothing
            }
        }
    }

    func askAboutRunningDownload() {
        let alert = NSAlert()
        alert.messageText = "A market data download is running"
        alert.informativeText = "StocksHighIV installs the update as soon as the download finishes, which can take an hour. "
            + "You can stop the download instead and install now: a scan resumes where it left off the next time you download."
        alert.addButton(withTitle: "Install When Finished")
        alert.addButton(withTitle: "Stop Download and Install Now")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertSecondButtonReturn {
            updateBackend("shutdown") { _ in
                guard let install = self.pendingInstall else { return }
                self.pendingInstall = nil
                install()
            }
        } else {
            updateTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { _ in self.reserveUpdate(afterAsking: true) }
        }
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        updateTimer?.invalidate()
        pendingInstall = nil
        updateBackend("cancel-update") { _ in }
    }


    func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "StocksHighIV could not start"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let submenu = NSMenu()
        submenu.addItem(withTitle: "Open dashboard", action: #selector(openDashboard), keyEquivalent: "o").target = self
        submenu.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates(_:)), keyEquivalent: ",").target = self
        submenu.addItem(withTitle: "Quit StocksHighIV", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = submenu
        NSApp.mainMenu = menu
        do {
            let fm = FileManager.default
            let support = try fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                     appropriateFor: nil, create: true).appendingPathComponent("StocksHighIV")
            try fm.createDirectory(at: support, withIntermediateDirectories: true)
            let payload = Bundle.main.resourceURL!.appendingPathComponent("project")
            let revision = Bundle.main.object(forInfoDictionaryKey: "StocksHighIVRevision") as! String
            let workspace = support.appendingPathComponent("versions/" + revision)
            backendIdentity = SHA256.hash(data: Data(workspace.resolvingSymlinksInPath().path.utf8)).map { String(format: "%02x", $0) }.joined()
            if !fm.fileExists(atPath: workspace.path) {
                try fm.createDirectory(at: workspace.deletingLastPathComponent(), withIntermediateDirectories: true)
                let staging = support.appendingPathComponent(UUID().uuidString)
                try fm.copyItem(at: payload, to: staging)
                try fm.moveItem(at: staging, to: workspace)
            }
            for name in ["data", "output"] {
                let shared = support.appendingPathComponent(name)
                try fm.createDirectory(at: shared, withIntermediateDirectories: true)
                let link = workspace.appendingPathComponent(name)
                if !fm.fileExists(atPath: link.path) {
                    try fm.createSymbolicLink(at: link, withDestinationURL: shared)
                }
            }
            let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
            let python = candidates.first { path in
                let check = Process()
                check.executableURL = URL(fileURLWithPath: path)
                check.arguments = ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"]
                check.standardOutput = FileHandle.nullDevice
                check.standardError = FileHandle.nullDevice
                do { try check.run(); check.waitUntilExit(); return check.terminationStatus == 0 }
                catch { return false }
            }
            guard let python else {
                fail("Install Python 3.11 or newer from python.org, then reopen StocksHighIV.")
                return
            }
            let logURL = support.appendingPathComponent("launcher.log")
            if !fm.fileExists(atPath: logURL.path) { fm.createFile(atPath: logURL.path, contents: nil) }
            log = try FileHandle(forWritingTo: logURL)
            log?.seekToEndOfFile()
            let child = Process()
            child.executableURL = URL(fileURLWithPath: python)
            child.arguments = [workspace.appendingPathComponent("launch.py").path]
            child.currentDirectoryURL = workspace
            child.standardOutput = log
            child.standardError = log
            child.environment = ProcessInfo.processInfo.environment.merging(["PYTHONUNBUFFERED": "1"]) { _, new in new }
            child.terminationHandler = { task in
                DispatchQueue.main.async {
                    if task.terminationStatus != 0 {
                        self.fail("See " + logURL.path + " for details. If another copy is running, quit it first.")
                    } else { NSApp.terminate(nil) }
                }
            }
            process = child
            try child.run()
            updaterController = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: self, userDriverDelegate: nil)
        } catch { fail(error.localizedDescription) }
    }

    @objc func openDashboard() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8932/")!)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openDashboard()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let child = process, child.isRunning {
            child.terminationHandler = nil
            child.interrupt() // Python finally closes the server and its download child.
            child.waitUntilExit()
        }
        try? log?.close()
    }
}

let app = NSApplication.shared
let delegate = Launcher()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
