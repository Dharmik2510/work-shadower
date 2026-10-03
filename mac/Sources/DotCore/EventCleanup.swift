import Foundation

// MARK: - Key combos

/// Canonical key-combo strings: modifiers in the fixed order
/// `cmd`, `ctrl`, `alt`, `shift`, then the key, joined by `+` ("cmd+shift+s").
/// Named keys: enter, tab, esc, space, delete, forward_delete, up, down, left,
/// right, home, end, page_up, page_down, f1...f12. Letters are lowercase.
public struct KeyCombo: Equatable {
    public var cmd: Bool
    public var ctrl: Bool
    public var alt: Bool
    public var shift: Bool
    public var key: String

    public init(cmd: Bool = false, ctrl: Bool = false, alt: Bool = false, shift: Bool = false, key: String) {
        self.cmd = cmd
        self.ctrl = ctrl
        self.alt = alt
        self.shift = shift
        self.key = KeyCombo.normalizeKeyName(key)
    }

    public var string: String {
        var parts: [String] = []
        if cmd { parts.append("cmd") }
        if ctrl { parts.append("ctrl") }
        if alt { parts.append("alt") }
        if shift { parts.append("shift") }
        parts.append(key)
        return parts.joined(separator: "+")
    }

    public var hasCommandOrControl: Bool { return cmd || ctrl }

    /// Parses "cmd+s", "Command+Shift+S", "⌘S", "ctrl+alt+delete". Returns nil
    /// when there is no non-modifier key.
    public static func parse(_ raw: String) -> KeyCombo? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        var combo = KeyCombo(key: "")
        // Symbol prefixes (⌘⌃⌥⇧) are allowed without separators.
        let symbols: [Character: WritableKeyPath<KeyCombo, Bool>] = ["⌘": \.cmd, "⌃": \.ctrl, "⌥": \.alt, "⇧": \.shift]
        while let first = s.first, let kp = symbols[first] {
            combo[keyPath: kp] = true
            s.removeFirst()
        }
        let parts: [String]
        if s == "+" {
            parts = ["+"]
        } else {
            parts = s.split(separator: "+", omittingEmptySubsequences: true).map { String($0) }
        }
        var key: String?
        for p in parts {
            switch p.lowercased().trimmingCharacters(in: .whitespaces) {
            case "cmd", "command", "meta", "super", "win": combo.cmd = true
            case "ctrl", "control", "ctl": combo.ctrl = true
            case "alt", "opt", "option": combo.alt = true
            case "shift": combo.shift = true
            default: key = p
            }
        }
        guard let k = key, !k.isEmpty else { return nil }
        combo.key = normalizeKeyName(k)
        return combo
    }

    public static func normalizeKeyName(_ raw: String) -> String {
        let k = raw.trimmingCharacters(in: .whitespaces).lowercased()
        switch k {
        case "return", "enter", "↩", "⏎": return "enter"
        case "escape", "esc", "⎋": return "esc"
        case "tab", "⇥": return "tab"
        case "space", " ", "spacebar": return "space"
        case "backspace", "delete", "del", "⌫": return "delete"
        case "forwarddelete", "forward_delete", "⌦": return "forward_delete"
        case "arrowup", "up", "↑": return "up"
        case "arrowdown", "down", "↓": return "down"
        case "arrowleft", "left", "←": return "left"
        case "arrowright", "right", "→": return "right"
        case "pageup", "page_up": return "page_up"
        case "pagedown", "page_down": return "page_down"
        default: return k
        }
    }
}

// MARK: - Typing buffer

/// Accumulates keystrokes for one focused element until it is flushed as a
/// single `type` event. Pure logic; the recorder feeds it.
public struct TypingBuffer {
    public private(set) var text: String = ""
    public private(set) var element: ElementInfo?
    public private(set) var elementKey: String?
    public private(set) var startedAt: Date?

    public init() {}

    public var isEmpty: Bool { return text.isEmpty }

    /// Starts buffering for `key` (an opaque identity of the focused element).
    /// Returns the previous buffer content as a flush if the element changed.
    public mutating func focus(elementKey key: String, element: ElementInfo) -> (text: String, element: ElementInfo?, startedAt: Date?)? {
        if key == elementKey { return nil }
        let flushed = flush()
        elementKey = key
        self.element = element
        return flushed
    }

    public mutating func append(_ chars: String, at date: Date) {
        guard let el = element, !el.isSecure else { return }
        if text.isEmpty { startedAt = date }
        text += chars
    }

    public mutating func backspace() {
        if !text.isEmpty { text.removeLast() }
    }

    /// Returns accumulated text (if any) and clears the text, keeping focus.
    public mutating func flush() -> (text: String, element: ElementInfo?, startedAt: Date?)? {
        defer {
            text = ""
            startedAt = nil
        }
        guard !text.isEmpty else { return nil }
        if let el = element, el.isSecure { return nil }
        return (text, element, startedAt)
    }

    /// Forget the focused element entirely (e.g. app switch).
    public mutating func reset() -> (text: String, element: ElementInfo?, startedAt: Date?)? {
        let f = flush()
        element = nil
        elementKey = nil
        return f
    }
}

// MARK: - Key moments (screenshot triggers)

public enum KeyMoments {
    /// Labels for which we screenshot *before* a click (destructive/committing).
    static let criticalWords: [String] = [
        "submit", "save", "send", "confirm", "delete", "remove", "approve", "pay",
        "publish", "post", "transfer", "sign", "finish", "place order", "apply",
    ]

    public static func isCriticalLabel(_ label: String?) -> Bool {
        guard let label = label?.lowercased(), !label.isEmpty else { return false }
        let words = label.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map { String($0) }
        for w in criticalWords {
            if w.contains(" ") {
                if label.contains(w) { return true }
            } else if words.contains(w) {
                return true
            }
        }
        return false
    }

    static let buttonRoles: Set<String> = ["AXButton", "AXMenuItem", "AXLink", "AXPopUpButton", "AXMenuButton"]

    public static func shouldScreenshotBeforeClick(role: String?, label: String?) -> Bool {
        guard let role = role, buttonRoles.contains(role) else { return false }
        return isCriticalLabel(label)
    }
}

// MARK: - Event cleanup

/// Normalizes a raw event log before it is queued:
/// - drops events from ignored bundle ids (Dot itself)
/// - drops empty `type` events and merges consecutive `type` events on the same element
/// - collapses repeated `app_activate` for the same app and repeated `url_change`/`window_open`
/// - strips URL query strings, applies PII redaction
/// - caps at `maxEvents` and renumbers `seq` from 1
public struct EventCleaner {
    public var ignoredBundleIDs: Set<String>
    public var maxEvents: Int
    public var redactor: Redactor

    public init(ignoredBundleIDs: Set<String> = [], maxEvents: Int = 5000, redactor: Redactor = .shared) {
        self.ignoredBundleIDs = ignoredBundleIDs
        self.maxEvents = maxEvents
        self.redactor = redactor
    }

    public func clean(_ raw: [RecordedEvent]) -> [RecordedEvent] {
        let sorted = raw.enumerated().sorted { a, b in
            if a.element.ts != b.element.ts { return a.element.ts < b.element.ts }
            if a.element.seq != b.element.seq { return a.element.seq < b.element.seq }
            return a.offset < b.offset
        }.map { $0.element }

        var out: [RecordedEvent] = []
        for var e in sorted {
            if let b = e.app?.bundleID, ignoredBundleIDs.contains(b) { continue }
            if let url = e.url { e.url = URLSanitizer.strip(url) }

            switch e.type {
            case .type:
                if e.element?.isSecure == true {
                    // Never keep text for secure fields; an empty secure "type" is noise.
                    continue
                }
                guard let t = e.text, !t.isEmpty else { continue }
                if let last = out.last, last.type == .type, sameElement(last, e) {
                    out[out.count - 1].text = (last.text ?? "") + t
                    continue
                }
            case .appActivate:
                // Same app re-activated with nothing in between: noise.
                if let last = out.last, last.type == .appActivate, last.app?.bundleID == e.app?.bundleID {
                    continue
                }
            case .urlChange:
                guard let u = e.url, !u.isEmpty else { continue }
                if let lastURL = out.last(where: { $0.type == .urlChange })?.url, lastURL == u { continue }
            case .windowOpen:
                if let last = out.last, last.type == .windowOpen,
                   last.window?.title == e.window?.title, last.app?.bundleID == e.app?.bundleID {
                    continue
                }
            case .key:
                guard let k = e.key, !k.isEmpty else { continue }
                if let combo = KeyCombo.parse(k) { e.key = combo.string }
            default:
                break
            }
            out.append(e)
        }

        // Redact after merging so PII split across keystroke chunks is caught.
        out = out.map { redactor.redact(event: $0) }
        if out.count > maxEvents { out = Array(out.prefix(maxEvents)) }
        for i in out.indices { out[i].seq = i + 1 }
        return out
    }

    private func sameElement(_ a: RecordedEvent, _ b: RecordedEvent) -> Bool {
        guard let ea = a.element, let eb = b.element else { return false }
        return a.app?.bundleID == b.app?.bundleID
            && ea.role == eb.role && ea.label == eb.label
            && ea.identifier == eb.identifier && ea.path == eb.path
    }
}
