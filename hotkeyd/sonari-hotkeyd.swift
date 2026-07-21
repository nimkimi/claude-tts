// sonari-hotkeyd.swift
// Sonari Phase 2 global-hotkey daemon.
//
// Reads ~/.sonari/hotkeyd.resolved.json — an array of
//   { "action": String, "keyCode": Int, "modifiers": Int, "message": String }
// produced by sonari.keymap.write_resolved(). For each entry it registers a
// Carbon global hotkey (RegisterEventHotKey: fires system-wide, consumes only
// the registered combo, needs NO macOS permission). On fire it reads the
// daemon's port + token from ~/.sonari/daemon.lock and writes the session
// token plus the entry's `message` (each newline-terminated) to the speechd
// localhost-TCP listener at 127.0.0.1 (best-effort; errors ignored).
//
// Build: swiftc hotkeyd/sonari-hotkeyd.swift -o ~/.sonari/sonari-hotkeyd
// Run:   the com.sonari.hotkeyd LaunchAgent (Aqua session, .accessory policy).

import Carbon
import Cocoa

let kHotKeySignature: OSType = 0x534F4E49  // 'SONI'

struct HotkeyEntry {
    let action: String
    let keyCode: UInt32
    let modifiers: UInt32
    let message: String
}

func sonariDir() -> String {
    return (NSHomeDirectory() as NSString).appendingPathComponent(".sonari")
}

func resolvedPath() -> String {
    return (sonariDir() as NSString).appendingPathComponent("hotkeyd.resolved.json")
}

// Parse the resolved JSON array into HotkeyEntry values.
func loadEntries() -> [HotkeyEntry] {
    guard let data = FileManager.default.contents(atPath: resolvedPath()) else {
        FileHandle.standardError.write(
            "hotkeyd: cannot read \(resolvedPath())\n".data(using: .utf8)!)
        return []
    }
    guard let parsed = try? JSONSerialization.jsonObject(with: data),
          let array = parsed as? [[String: Any]] else {
        FileHandle.standardError.write("hotkeyd: malformed resolved JSON\n".data(using: .utf8)!)
        return []
    }
    var entries: [HotkeyEntry] = []
    for obj in array {
        guard let action = obj["action"] as? String,
              let keyCode = obj["keyCode"] as? Int,
              let modifiers = obj["modifiers"] as? Int,
              let message = obj["message"] as? String else {
            continue
        }
        entries.append(HotkeyEntry(
            action: action,
            keyCode: UInt32(keyCode),
            modifiers: UInt32(modifiers),
            message: message))
    }
    return entries
}

// Read ~/.sonari/daemon.lock for the daemon's ephemeral port + session token.
func lockInfo() -> (port: UInt16, token: String)? {
    let path = (sonariDir() as NSString).appendingPathComponent("daemon.lock")
    guard let data = FileManager.default.contents(atPath: path),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let port = obj["port"] as? Int,
          let token = obj["token"] as? String else { return nil }
    return (UInt16(port), token)
}

// Best-effort: connect to the speechd localhost-TCP listener and write
// the session token followed by one newline-JSON message line. Returns
// whether the line was handed to a connected socket (the witness beat
// counts consecutive failures; every other caller ignores it).
@discardableResult
func sendMessage(_ message: String) -> Bool {
    guard let info = lockInfo() else { return false }
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = info.port.bigEndian
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr)
    let connected = withUnsafePointer(to: &addr) { aptr -> Int32 in
        aptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sptr in
            connect(fd, sptr, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    if connected != 0 { return false }
    let line = info.token + "\n" + message + "\n"
    _ = line.withCString { write(fd, $0, strlen($0)) }
    return true
}

// --- Focus-watcher: report which terminal (tty / iTerm id) has OS keyboard focus.
// Cheap NSWorkspace gate (no permission); the Apple-Event read is delegated to
// sonari-raise (reuses its Automation grant) on a background queue so the slow read
// never blocks the hotkey run loop. Sends only on change. ---
let terminalBundles: [String: String] = [
    "com.apple.Terminal": "Apple_Terminal",
    "com.googlecode.iterm2": "iTerm.app",
]
let focusQueue = DispatchQueue(label: "sonari.focuswatch")
var lastFocusLine: String? = nil   // touched only on focusQueue

func raiseBinPath() -> String {
    return (sonariDir() as NSString).appendingPathComponent("sonari-raise")
}

// Run `sonari-raise <arg>`, return trimmed stdout, or nil on non-zero/failure.
func readFront(_ arg: String) -> String? {
    let bin = raiseBinPath()
    guard FileManager.default.isExecutableFile(atPath: bin) else { return nil }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: bin)
    p.arguments = [arg]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return nil }
    p.waitUntilExit()
    if p.terminationStatus != 0 { return nil }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    let s = String(data: data, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return s.isEmpty ? nil : s
}

func jsonLine(_ obj: [String: Any]) -> String? {
    guard let d = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
    return String(data: d, encoding: .utf8)
}

// Read frontmost app on the main thread (AppKit), then do the slow read off-thread.
func pollFocus() {
    let bundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    let term = terminalBundles[bundle]
    focusQueue.async {
        var msg: [String: Any]
        if term == "Apple_Terminal", let tty = readFront("--front-tty") {
            msg = ["type": "os_focus", "term_program": "Apple_Terminal", "tty": tty]
        } else if term == "iTerm.app", let gid = readFront("--front-iterm") {
            msg = ["type": "os_focus", "term_program": "iTerm.app", "iterm_session_id": gid]
        } else if term == nil {
            msg = ["type": "os_focus", "focused": false]
        } else {
            return   // terminal frontmost but read failed -> keep last state (stale-safe)
        }
        guard let line = jsonLine(msg) else { return }
        if line == lastFocusLine { return }     // change-detection: no idle traffic
        lastFocusLine = line
        sendMessage(line)
    }
}

// --- Witness (daemon-death direction): ping speechd on the focus timer's
// cadence (every 10 ticks ~ 5 s); at 3 consecutive send failures (~ 15 s)
// sound the alarm ONCE; re-arm only after a later successful send. The alarm
// is a raw shell-out — the daemon is the thing that just died, so nothing may
// route through it. /usr/bin/env resolves afplay/say via PATH. Config rides
// the resolved file's witness_config entry; the compiled-in defaults below
// mean a stale resolved file can never silently disable the alarm. ---
struct WitnessConfig {
    var alarmAsset = "/System/Library/Sounds/Hero.aiff"   // compiled-in defaults
    var alarmWords = "Sonari is down."                    // (stale-file safety)
    var alarmEnabled = true
}

func loadWitnessConfig() -> WitnessConfig {
    var cfg = WitnessConfig()
    guard let data = FileManager.default.contents(atPath: resolvedPath()),
          let parsed = try? JSONSerialization.jsonObject(with: data),
          let array = parsed as? [[String: Any]] else { return cfg }
    for obj in array where (obj["action"] as? String) == "witness_config" {
        if let a = obj["alarmAsset"] as? String { cfg.alarmAsset = a }
        if let w = obj["alarmWords"] as? String { cfg.alarmWords = w }
        if let e = obj["alarmEnabled"] as? Bool { cfg.alarmEnabled = e }
    }
    return cfg
}

let witnessConfig = loadWitnessConfig()
let witnessPingEveryTicks = 10       // 0.5 s focus ticks -> ~5 s cadence
let witnessFailAfter = 3             // 3 failed sends -> ~15 s
var witnessTick = 0
var witnessFailures = 0
var witnessAlarmed = false

func witnessShellOut(_ tool: String, _ argument: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    p.arguments = [tool, argument]
    try? p.run()
}

func fireWitnessAlarm() {
    guard witnessConfig.alarmEnabled else { return }
    witnessShellOut("afplay", witnessConfig.alarmAsset)
    witnessShellOut("say", witnessConfig.alarmWords)
}

func witnessBeat() {
    witnessTick += 1
    if witnessTick % witnessPingEveryTicks != 0 { return }
    if sendMessage("{\"v\": 1, \"type\": \"witness_ping\"}") {
        witnessFailures = 0
        witnessAlarmed = false          // recovery re-arms
    } else {
        witnessFailures += 1
        if witnessFailures >= witnessFailAfter && !witnessAlarmed {
            witnessAlarmed = true       // once, until a successful send
            fireWitnessAlarm()
        }
    }
}

// --- Chooser-mode FSM (spec §5). On a chooser_step_* fire: enter mode + send the
// step. While in mode: ⌃⌘1-9 are dynamically registered (they must NEVER shadow
// other apps otherwise); a ~40 ms poll of the CURRENT modifier state
// (NSEvent.modifierFlags — permission-free: no event tap, no new TCC, verified
// 2026-07-14) detects ⌃ or ⌘ release -> CHOOSER_COMMIT; a 30 s cap ->
// CHOOSER_CANCEL. Shift is deliberately NOT monitored: ⇧ toggles step direction
// and is released between steps — its release must never commit. Exit always
// unregisters the digits and stops both timers. The 0.5 s focus poller below is
// untouched. ---
let chooserDigitKeyCodes: [Int: UInt32] = [   // kVK_ANSI_1...kVK_ANSI_9
    1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25,
]
let kChooserDigitIDBase: UInt32 = 1000        // id space above the resolved entries
var chooserMode = false
var chooserDigitRefs: [EventHotKeyRef] = []
var chooserReleaseTimer: Timer? = nil
var chooserCapTimer: Timer? = nil
var chooserRequiredFlags: NSEvent.ModifierFlags = []

func carbonToNSFlags(_ mask: UInt32) -> NSEvent.ModifierFlags {
    var flags: NSEvent.ModifierFlags = []
    if mask & 4096 != 0 { flags.insert(.control) }   // controlKey
    if mask & 256 != 0 { flags.insert(.command) }    // cmdKey
    if mask & 2048 != 0 { flags.insert(.option) }    // optionKey
    // shiftKey (512) EXCLUDED: its release steps direction, never commits.
    return flags
}

func chooserSend(_ obj: [String: Any]) {
    if let line = jsonLine(obj) { sendMessage(line) }
}

func exitChooserMode() {
    guard chooserMode else { return }
    chooserMode = false
    for ref in chooserDigitRefs { UnregisterEventHotKey(ref) }
    chooserDigitRefs = []
    chooserReleaseTimer?.invalidate()
    chooserReleaseTimer = nil
    chooserCapTimer?.invalidate()
    chooserCapTimer = nil
}

func enterChooserMode(entryModifiers: UInt32) {
    guard !chooserMode else { return }
    chooserMode = true
    chooserRequiredFlags = carbonToNSFlags(entryModifiers)
    // Register the digits on the entry chord minus shift (⇧Tab also enters mode).
    let digitMods = entryModifiers & ~UInt32(512)
    for (digit, keyCode) in chooserDigitKeyCodes {
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: kHotKeySignature,
                                     id: kChooserDigitIDBase + UInt32(digit))
        if RegisterEventHotKey(keyCode, digitMods, hotKeyID,
                               GetApplicationEventTarget(), 0, &ref) == noErr,
           let r = ref {
            chooserDigitRefs.append(r)
        }
    }
    // Release poll: commit the moment a required modifier is observed released.
    let poll = Timer(timeInterval: 0.04, repeats: true) { _ in
        let held = NSEvent.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if !chooserRequiredFlags.isSubset(of: held) {
            chooserSend(["type": "chooser_commit"])
            exitChooserMode()
        }
    }
    RunLoop.main.add(poll, forMode: .common)
    chooserReleaseTimer = poll
    // Safety cap: a wedged chord cancels rather than committing somewhere random.
    let cap = Timer(timeInterval: 30.0, repeats: false) { _ in
        chooserSend(["type": "chooser_cancel"])
        exitChooserMode()
    }
    RunLoop.main.add(cap, forMode: .common)
    chooserCapTimer = cap
}

// Index entries by their hotkey id so the handler can look up the message.
var entriesByID: [UInt32: HotkeyEntry] = [:]

let hotKeyHandler: EventHandlerUPP = { (_ nextHandler, _ theEvent, _ userData) -> OSStatus in
    var hkID = EventHotKeyID()
    let status = GetEventParameter(
        theEvent,
        EventParamName(kEventParamDirectObject),
        EventParamType(typeEventHotKeyID),
        nil,
        MemoryLayout<EventHotKeyID>.size,
        nil,
        &hkID
    )
    if status == noErr && hkID.signature == kHotKeySignature {
        if hkID.id >= kChooserDigitIDBase {
            // A chooser digit — registered only while in mode: teleport + exit (§5).
            let digit = Int(hkID.id - kChooserDigitIDBase)
            chooserSend(["type": "chooser_digit", "digit": digit])
            exitChooserMode()
        } else if let entry = entriesByID[hkID.id] {
            if entry.action == "chooser_step_next" || entry.action == "chooser_step_prev" {
                enterChooserMode(entryModifiers: entry.modifiers)
                sendMessage(entry.message)    // the first step opens daemon-side
            } else {
                sendMessage(entry.message)
            }
        }
    }
    return noErr
}

// 1. Install the keyboard event handler for hotkey-pressed events.
var eventType = EventTypeSpec(
    eventClass: OSType(kEventClassKeyboard),
    eventKind: UInt32(kEventHotKeyPressed)
)
let installStatus = InstallEventHandler(
    GetApplicationEventTarget(),
    hotKeyHandler,
    1,
    &eventType,
    nil,
    nil
)
guard installStatus == noErr else {
    FileHandle.standardError.write(
        "hotkeyd: InstallEventHandler failed: \(installStatus)\n".data(using: .utf8)!)
    exit(1)
}

// 2. Register each resolved entry. Keep the refs alive for the process lifetime.
var hotKeyRefs: [EventHotKeyRef?] = []
let entries = loadEntries()
for (index, entry) in entries.enumerated() {
    let id = UInt32(index)
    entriesByID[id] = entry
    var ref: EventHotKeyRef?
    let hotKeyID = EventHotKeyID(signature: kHotKeySignature, id: id)
    let regStatus = RegisterEventHotKey(
        entry.keyCode,
        entry.modifiers,
        hotKeyID,
        GetApplicationEventTarget(),
        0,
        &ref
    )
    if regStatus != noErr {
        // A claimed combo: log and continue with the rest.
        FileHandle.standardError.write(
            "hotkeyd: RegisterEventHotKey failed for id \(id) (status \(regStatus))\n"
                .data(using: .utf8)!)
        continue
    }
    hotKeyRefs.append(ref)
}

FileHandle.standardError.write(
    "hotkeyd: registered \(hotKeyRefs.count)/\(entries.count) hotkeys\n".data(using: .utf8)!)

// 3. Run the Carbon event loop headlessly (no Dock icon).
let app = NSApplication.shared
app.setActivationPolicy(.accessory)

// Focus-watcher: app-activation events + a light poll (catches window/tab switches
// WITHIN a terminal app, which fire no NSWorkspace notification).
NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didActivateApplicationNotification,
    object: nil, queue: .main) { _ in pollFocus() }
let focusTimer = Timer(timeInterval: 0.5, repeats: true) { _ in
    pollFocus()
    witnessBeat()
}
RunLoop.main.add(focusTimer, forMode: .common)
pollFocus()   // report initial focus

app.run()
