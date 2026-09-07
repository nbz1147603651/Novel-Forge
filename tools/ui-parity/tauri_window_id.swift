import Cocoa
import CoreGraphics
import Foundation

struct WindowRecord: Codable {
    let windowId: UInt32
    let owner: String
    let ownerPid: Int32
    let title: String
    let x: Int
    let y: Int
    let width: Int
    let height: Int
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    fputs("Usage: tauri_window_id.swift OWNER [TITLE] [PID]\\n", stderr)
    exit(64)
}

let expectedOwner = arguments[1]
let expectedTitle = arguments.count >= 3 && !arguments[2].isEmpty ? arguments[2] : nil
let expectedPid = arguments.count >= 4 ? Int32(arguments[3]) : nil
let windowList = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
    as? [[String: Any]] ?? []

var candidate: WindowRecord?

for window in windowList {
    guard let owner = window[kCGWindowOwnerName as String] as? String,
          owner == expectedOwner,
          let ownerPid = window[kCGWindowOwnerPID as String] as? NSNumber,
          let windowId = window[kCGWindowNumber as String] as? NSNumber,
          let bounds = window[kCGWindowBounds as String] as? [String: Any]
    else {
        continue
    }
    let title = window[kCGWindowName as String] as? String ?? ""
    if let expectedTitle, title != expectedTitle {
        continue
    }
    if let expectedPid, ownerPid.int32Value != expectedPid {
        continue
    }
    func coordinate(_ key: String) -> Int {
        (bounds[key] as? NSNumber)?.intValue ?? 0
    }
    let record = WindowRecord(
        windowId: windowId.uint32Value,
        owner: owner,
        ownerPid: ownerPid.int32Value,
        title: title,
        x: coordinate("X"),
        y: coordinate("Y"),
        width: coordinate("Width"),
        height: coordinate("Height")
    )
    let area = Int64(record.width) * Int64(record.height)
    let candidateArea = candidate.map { Int64($0.width) * Int64($0.height) } ?? -1
    if area > candidateArea {
        candidate = record
    }
}

guard let candidate else {
    exit(1)
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let data = try encoder.encode(candidate)
print(String(decoding: data, as: UTF8.self))
