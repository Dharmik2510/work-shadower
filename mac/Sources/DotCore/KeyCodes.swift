import Foundation

/// macOS virtual key codes (kVK_* from HIToolbox Events.h, ANSI/US layout)
/// for replaying `key` actions. Kept here (pure data) so it's unit-testable.
public enum KeyCodes {
    public static let table: [String: UInt16] = [
        "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
        "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
        "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18,
        "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F, "u": 0x20,
        "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29,
        "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "`": 0x32,
        "enter": 0x24, "tab": 0x30, "space": 0x31, "delete": 0x33, "esc": 0x35,
        "forward_delete": 0x75, "home": 0x73, "end": 0x77, "page_up": 0x74, "page_down": 0x79,
        "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
        "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62,
        "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    ]

    public static func code(for key: String) -> UInt16? {
        return table[KeyCombo.normalizeKeyName(key)]
    }

    /// Reverse lookup for named (non-character) keys, used by the recorder.
    public static func namedKey(for code: UInt16) -> String? {
        switch code {
        case 0x24, 0x4C: return "enter"   // return, keypad enter
        case 0x30: return "tab"
        case 0x31: return "space"
        case 0x33: return "delete"
        case 0x35: return "esc"
        case 0x75: return "forward_delete"
        case 0x73: return "home"
        case 0x77: return "end"
        case 0x74: return "page_up"
        case 0x79: return "page_down"
        case 0x7B: return "left"
        case 0x7C: return "right"
        case 0x7D: return "down"
        case 0x7E: return "up"
        case 0x7A: return "f1"
        case 0x78: return "f2"
        case 0x63: return "f3"
        case 0x76: return "f4"
        case 0x60: return "f5"
        case 0x61: return "f6"
        case 0x62: return "f7"
        case 0x64: return "f8"
        case 0x65: return "f9"
        case 0x6D: return "f10"
        case 0x67: return "f11"
        case 0x6F: return "f12"
        default: return nil
        }
    }
}
