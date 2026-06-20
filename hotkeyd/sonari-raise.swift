// hotkeyd/sonari-raise.swift
// Sonari focus-follow helper. Holds a dedicated, recognizable Automation grant
// (so the consent dialog reads as Sonari's, not a shared /usr/bin/osascript).
//
//   sonari-raise <tty>   raise the visible Terminal window whose selected tab's
//                        tty == <tty>. Exit: 0 raised, 1 not-found/missed,
//                        3 Automation denied (-1743), 4 other AppleScript error.
//   sonari-raise --check send one harmless controlling Apple Event to surface /
//                        test the Automation grant. Exit: 0 granted, 3 denied, 4 other.
//   sonari-raise --iterm <id>   raise the iTerm2 session whose bare GUID matches
//                        <id> (the captured ITERM_SESSION_ID, "wNtNpN:GUID"; the
//                        bare GUID is the part after the last ':'). The shipped
//                        iterm2:///reveal URL lands on the WRONG session on macOS
//                        Tahoe; this AppleScript recipe is empirically validated.
//                        Exit: 0 raised, 1 not-found/missed, 3 denied, 4 other.
//   sonari-raise --check-iterm  same as --check but for iTerm2's grant (separate
//                        from Terminal's). Exit: 0 granted, 3 denied, 4 other.
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

if args.count >= 2 && args[1] == "--check-iterm" {
    let (_, code) = runAppleScript("tell application \"iTerm2\" to count windows")
    exit(code)
}

// --iterm <id>: raise the iTerm2 session matching the bare GUID of <id>. Dispatched
// before the generic <tty> path so the id isn't treated as a Terminal tty.
if args.count >= 2 && args[1] == "--iterm" {
    guard args.count >= 3 else {
        FileHandle.standardError.write(
            "usage: sonari-raise --iterm <session-id>\n".data(using: .utf8)!)
        exit(2)
    }
    // iTerm2's `id of session` is the BARE GUID: strip up to and including the
    // last ':' of "wNtNpN:GUID". If there's no ':', use the whole arg.
    let rawID = args[2]
    let bareID: String
    if let colon = rawID.lastIndex(of: ":") {
        bareID = String(rawID[rawID.index(after: colon)...])
    } else {
        bareID = rawID
    }
    let itermRecipe = """
    try
        tell application "iTerm2"
            set pickedW to missing value
            set pickedT to missing value
            set pickedS to missing value
            repeat with w in windows
                try
                    repeat with t in tabs of w
                        repeat with s in sessions of t
                            if (id of s) is "\(bareID)" then
                                set pickedW to w
                                set pickedT to t
                                set pickedS to s
                            end if
                        end repeat
                    end repeat
                end try
            end repeat
            if pickedW is missing value then return "NOTFOUND"
            tell pickedS to select
            tell pickedT to select
            set index of pickedW to 1
            activate
            delay 0.8
            if (id of current session of current tab of current window) is "\(bareID)" then
                return "OK"
            else
                return "MISS"
            end if
        end tell
    on error e number n
        return "ERR" & n
    end try
    """
    let (itermResult, itermCode) = runAppleScript(itermRecipe)
    if itermCode != 0 { exit(itermCode) }
    exit(itermResult == "OK" ? 0 : 1)
}

guard args.count >= 2 else {
    FileHandle.standardError.write(
        "usage: sonari-raise <tty> | --check | --iterm <session-id> | --check-iterm\n"
            .data(using: .utf8)!)
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
