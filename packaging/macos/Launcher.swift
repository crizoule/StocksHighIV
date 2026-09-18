import Cocoa

// The signed bundle stays immutable; Python and market data live in Application Support.
final class Launcher: NSObject, NSApplicationDelegate {
    var process: Process?
    var log: FileHandle?

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
