// Alice STEM Snip: メニューバーに常駐し、全体のショートカット（既定は Mathpix Snip と同じ ⌃⌘M・⌃⌘O・⌃⌘L・⌃⌘N）を受け付ける。
//
// - ショートカットは Carbon の RegisterEventHotKey で登録する（アクセシビリティの許可は要らない）。
// - 割り当ては ~/Library/Application Support/Alice STEM Reader/app.json の "global_hotkeys"（ビューアの設定画面が書く）。
//   2 秒ごとに更新時刻を見て、変わっていれば登録し直す。
// - 実際の処理は、このプロジェクトの Python（`python -m pdf_latex_overlay app-snip` など）に任せる。
// - `--background` を付けずに起動したとき（Launchpad・Spotlight から開いたとき）と、もう一度開いたときは、すぐ画面を Snip する。
//
// build-app が swiftc でコンパイルする（Resources/agent.json に Python の場所を書き込む）。
import Carbon.HIToolbox
import Cocoa

let defaultHotkeys: [String: String] = ["snip": "ctrl+cmd+m", "open": "ctrl+cmd+o", "latex": "ctrl+cmd+l", "markdown": "ctrl+cmd+n"]
let hotkeyOrder = ["snip", "open", "latex", "markdown"]
let launchAgentLabel = "io.github.alice-stem-reader.snip"

struct AgentConfig {
    var python: String
    var configPath: String
    var logPath: String
}

func loadAgentConfig() -> AgentConfig {
    let home = NSHomeDirectory()
    var d: [String: String] = [:]
    if let url = Bundle.main.url(forResource: "agent", withExtension: "json"),
       let data = try? Data(contentsOf: url),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String] {
        d = obj
    }
    return AgentConfig(
        python: d["python"] ?? "/usr/bin/python3",
        configPath: d["config"] ?? home + "/Library/Application Support/Alice STEM Reader/app.json",
        logPath: d["log"] ?? home + "/Library/Logs/Alice STEM Reader.log")
}

// キーの名前 → 仮想キーコード（US 配列の位置。JIS 配列でも英字・数字は同じ位置）
let keyCodes: [String: Int] = [
    "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D, "e": kVK_ANSI_E, "f": kVK_ANSI_F, "g": kVK_ANSI_G,
    "h": kVK_ANSI_H, "i": kVK_ANSI_I, "j": kVK_ANSI_J, "k": kVK_ANSI_K, "l": kVK_ANSI_L, "m": kVK_ANSI_M, "n": kVK_ANSI_N,
    "o": kVK_ANSI_O, "p": kVK_ANSI_P, "q": kVK_ANSI_Q, "r": kVK_ANSI_R, "s": kVK_ANSI_S, "t": kVK_ANSI_T, "u": kVK_ANSI_U,
    "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X, "y": kVK_ANSI_Y, "z": kVK_ANSI_Z,
    "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3, "4": kVK_ANSI_4, "5": kVK_ANSI_5, "6": kVK_ANSI_6,
    "7": kVK_ANSI_7, "8": kVK_ANSI_8, "9": kVK_ANSI_9, "space": kVK_Space,
    "f1": kVK_F1, "f2": kVK_F2, "f3": kVK_F3, "f4": kVK_F4, "f5": kVK_F5, "f6": kVK_F6, "f7": kVK_F7, "f8": kVK_F8,
    "f9": kVK_F9, "f10": kVK_F10, "f11": kVK_F11, "f12": kVK_F12, "f13": kVK_F13, "f14": kVK_F14, "f15": kVK_F15,
    "f16": kVK_F16, "f17": kVK_F17, "f18": kVK_F18, "f19": kVK_F19,
]

struct Hotkey {
    let code: UInt32
    let carbonMods: UInt32
    let key: String
    let cocoaMods: NSEvent.ModifierFlags
}

func parseHotkey(_ s: String) -> Hotkey? {
    let parts = s.lowercased().split(separator: "+").map(String.init)
    guard let key = parts.last, let code = keyCodes[key], parts.count >= 2 else { return nil }
    var carbon: UInt32 = 0
    var cocoa: NSEvent.ModifierFlags = []
    for m in parts.dropLast() {
        switch m {
        case "ctrl": carbon |= UInt32(controlKey); cocoa.insert(.control)
        case "alt": carbon |= UInt32(optionKey); cocoa.insert(.option)
        case "shift": carbon |= UInt32(shiftKey); cocoa.insert(.shift)
        case "cmd": carbon |= UInt32(cmdKey); cocoa.insert(.command)
        default: return nil
        }
    }
    return Hotkey(code: UInt32(code), carbonMods: carbon, key: key, cocoaMods: cocoa)
}

final class HotKeyCenter {
    static let shared = HotKeyCenter()
    private var refs: [EventHotKeyRef] = []
    var actions: [UInt32: () -> Void] = [:]
    private var installed = false

    func installHandler() {
        if installed { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            let st = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                       nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            if st == noErr {
                let id = hk.id
                DispatchQueue.main.async { HotKeyCenter.shared.actions[id]?() }
            }
            return noErr
        }, 1, &spec, nil, nil)
    }

    func unregisterAll() {
        for r in refs { UnregisterEventHotKey(r) }
        refs.removeAll()
        actions.removeAll()
    }

    func register(id: UInt32, hotkey: Hotkey, action: @escaping () -> Void) -> Bool {
        var ref: EventHotKeyRef?
        let hid = EventHotKeyID(signature: OSType(0x4153_5452), id: id)  // 'ASTR'
        let st = RegisterEventHotKey(hotkey.code, hotkey.carbonMods, hid, GetApplicationEventTarget(), 0, &ref)
        guard st == noErr, let r = ref else { return false }
        refs.append(r)
        actions[id] = action
        return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let cfg = loadAgentConfig()
    var statusItem: NSStatusItem?
    var timer: Timer?
    var lastMtime: Date?
    var current: [String: String] = [:]
    var failed: [String] = []
    var busy = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        HotKeyCenter.shared.installHandler()
        reloadHotkeys(force: true)
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in self?.reloadHotkeys(force: false) }
        if !CommandLine.arguments.contains("--background") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.run("snip") }
        }
    }

    // すでに動いているときに、もう一度開いた（Launchpad・Spotlight・Dock）: Snip する
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        run("snip")
        return false
    }

    func readHotkeys() -> [String: String] {
        var hk = defaultHotkeys
        if let data = FileManager.default.contents(atPath: cfg.configPath),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let g = obj["global_hotkeys"] as? [String: String] {
            for (k, v) in g where defaultHotkeys[k] != nil { hk[k] = v }
        }
        return hk
    }

    func reloadHotkeys(force: Bool) {
        let attrs = try? FileManager.default.attributesOfItem(atPath: cfg.configPath)
        let m = attrs?[.modificationDate] as? Date
        if !force && m == lastMtime { return }
        lastMtime = m
        let hk = readHotkeys()
        if !force && hk == current { return }
        current = hk
        let center = HotKeyCenter.shared
        center.unregisterAll()
        failed = []
        for (i, name) in hotkeyOrder.enumerated() {
            guard let s = hk[name], !s.isEmpty else { continue }
            guard let h = parseHotkey(s) else { failed.append(name); continue }
            let ok = center.register(id: UInt32(i + 1), hotkey: h) { [weak self] in self?.run(name) }
            if !ok { failed.append(name) }  // ほかのアプリが同じキーを使っている
        }
        buildMenu()
    }

    // ---- メニューバー
    func item(_ title: String, _ action: Selector, hotkey: String? = nil) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: action, keyEquivalent: "")
        it.target = self
        if let s = hotkey, let h = parseHotkey(s), h.key.count == 1 {
            it.keyEquivalent = h.key
            it.keyEquivalentModifierMask = h.cocoaMods
        }
        return it
    }

    func buildMenu() {
        if statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            if let b = statusItem?.button {
                if #available(macOS 11.0, *), let img = NSImage(systemSymbolName: "scissors", accessibilityDescription: "Alice STEM Snip") {
                    img.isTemplate = true
                    b.image = img
                } else {
                    b.title = "✂︎"
                }
                b.toolTip = "Alice STEM Snip"
            }
        }
        let menu = NSMenu()
        menu.addItem(item("Snip Screen", #selector(doSnip), hotkey: current["snip"]))
        menu.addItem(item("Open Alice STEM Reader", #selector(doOpen), hotkey: current["open"]))
        menu.addItem(item("New Snip (LaTeX)", #selector(doLatex), hotkey: current["latex"]))
        menu.addItem(item("New Snip (Markdown)", #selector(doMarkdown), hotkey: current["markdown"]))
        menu.addItem(item("Snip Dashboard", #selector(doDashboard)))
        if !failed.isEmpty {
            menu.addItem(NSMenuItem.separator())
            let w = NSMenuItem(title: "Shortcut unavailable (used by another app): " + failed.joined(separator: ", "), action: nil, keyEquivalent: "")
            w.isEnabled = false
            menu.addItem(w)
        }
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("Change Shortcuts…", #selector(doSettings)))
        let login = item("Start at Login", #selector(toggleLogin))
        login.state = FileManager.default.fileExists(atPath: launchAgentPath()) ? .on : .off
        menu.addItem(login)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("Quit Alice STEM Snip", #selector(doQuit)))
        statusItem?.menu = menu
    }

    @objc func doSnip() { run("snip") }
    @objc func doOpen() { run("open") }
    @objc func doLatex() { run("latex") }
    @objc func doMarkdown() { run("markdown") }
    @objc func doDashboard() { run("dashboard") }
    @objc func doSettings() { run("settings") }
    @objc func doQuit() { NSApp.terminate(nil) }

    // ---- ログイン時に起動（~/Library/LaunchAgents に plist を置く・外す）
    func launchAgentPath() -> String { NSHomeDirectory() + "/Library/LaunchAgents/\(launchAgentLabel).plist" }

    @objc func toggleLogin() {
        let path = launchAgentPath()
        if FileManager.default.fileExists(atPath: path) {
            try? FileManager.default.removeItem(atPath: path)
        } else {
            let exe = Bundle.main.executablePath ?? CommandLine.arguments[0]
            let plist: [String: Any] = ["Label": launchAgentLabel, "ProgramArguments": [exe, "--background"],
                                        "RunAtLoad": true, "ProcessType": "Interactive"]
            try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            if let data = try? PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0) {
                FileManager.default.createFile(atPath: path, contents: data)
            }
        }
        buildMenu()
    }

    // ---- Python に任せる
    func run(_ action: String) {
        var args: [String]
        switch action {
        case "snip":
            if busy { return }
            busy = true
            args = ["app-snip"]
        case "open": args = ["app-open", "--view", "reader"]
        case "latex": args = ["app-open", "--new", "latex"]
        case "markdown": args = ["app-open", "--new", "markdown"]
        case "dashboard": args = ["app-open", "--view", "snips"]
        case "settings": args = ["app-open", "--view", "settings"]
        default: return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: cfg.python)
        p.arguments = ["-m", "pdf_latex_overlay"] + args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        let logPath = cfg.logPath
        p.terminationHandler = { [weak self] proc in
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let out = String(data: data, encoding: .utf8) ?? ""
            self?.appendLog("[\(action)] exit \(proc.terminationStatus)\n" + out)
            DispatchQueue.main.async {
                if action == "snip" { self?.busy = false }
                // 5 は「読み取れなかった」（通知で知らせ済み）。それ以外の失敗は、理由を出す
                if proc.terminationStatus != 0 && proc.terminationStatus != 5 {
                    let tail = out.split(separator: "\n").suffix(6).joined(separator: "\n")
                    let msg = tail.isEmpty ? "Failed. See the log: " + logPath : tail
                    self?.alert("Alice STEM Snip", msg)
                }
            }
        }
        do {
            try p.run()
        } catch {
            if action == "snip" { busy = false }
            alert("Alice STEM Snip", "Could not start Python at \(cfg.python).\nRebuild the apps with `build-app`.\n\(error.localizedDescription)")
        }
    }

    func appendLog(_ s: String) {
        let line = "\(Date()) snip-agent \(s)\n"
        guard let data = line.data(using: .utf8) else { return }
        if let h = FileHandle(forWritingAtPath: cfg.logPath) {
            h.seekToEndOfFile()
            h.write(data)
            h.closeFile()
        } else {
            FileManager.default.createFile(atPath: cfg.logPath, contents: data)
        }
    }

    func alert(_ title: String, _ msg: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = msg
        a.alertStyle = .warning
        a.runModal()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
