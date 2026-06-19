// hotkeyd/sonari-raise.swift
// Sonari focus-follow helper. Holds a dedicated, recognizable Automation grant
// (so the consent dialog reads as Sonari's, not a shared /usr/bin/osascript).
//
//   sonari-raise <tty>   raise the visible Terminal window whose selected tab's
//                        tty == <tty>. Exit: 0 raised, 1 not-found/missed,
//                        3 Automation denied (-1743), 4 other AppleScript error.
//   sonari-raise --check send one harmless controlling Apple Event to surface /
//                        test the Automation grant. Exit: 0 granted, 3 denied, 4 other.
//
// AppleScript recipe is the empirically proven one (spec §3): match by tty,
// `set selected` + `set index ... to 1` + `activate`. NEVER `set frontmost of
// window` (it reverts the raise). Skip phantom windows (visible + tabs > 0).
//
// Build: swiftc hotkeyd/sonari-raise.swift -o ~/.sonari/sonari-raise

import Foundation

// Run an AppleScript; return (stringResult, exitCode). exitCode: 0 ok, 3 denied
// (-1743), 4 other error, 2 could-not-build-script.
func runAppleScript(_ src: String) -> (String, Int32) {
    var err: NSDictionary?
    guard let script = NSAppleScript(source: src) else { return ("", 2) }
    let desc = script.executeAndReturnError(&err)
    if let e = err {
        let n = (e[NSAppleScript.errorNumber] as? Int) ?? 0
        return ("ERR\(n)", n == -1743 ? 3 : 4)
    }
    return (desc.stringValue ?? "", 0)
}

let args = CommandLine.arguments

if args.count >= 2 && args[1] == "--check" {
    let (_, code) = runAppleScript("tell application \"Terminal\" to count windows")
    exit(code)
}

guard args.count >= 2 else {
    FileHandle.standardError.write(
        "usage: sonari-raise <tty> | --check\n".data(using: .utf8)!)
    exit(2)
}

let target = args[1]
let recipe = """
try
    tell application "Terminal"
        set picked to missing value
        repeat with w in windows
            try
                if visible of w and (count of tabs of w) > 0 then
                    if (tty of selected tab of w) is "\(target)" then
                        set picked to w
                        exit repeat
                    end if
                end if
            end try
        end repeat
        if picked is missing value then return "NOTFOUND"
        set selected of (selected tab of picked) to true
        set index of picked to 1
        activate
        delay 0.2
        if (tty of selected tab of front window) is "\(target)" then
            return "OK"
        else
            return "MISS"
        end if
    end tell
on error e number n
    return "ERR" & n
end try
"""

let (result, code) = runAppleScript(recipe)
if code != 0 { exit(code) }
exit(result == "OK" ? 0 : 1)
