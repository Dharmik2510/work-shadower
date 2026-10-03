import Foundation

/// Client-side PII redaction (CONTRACT "Redaction rule"). The server re-checks.
///
/// - never send text for `value_kind = secure`
/// - replace emails, card-like digit runs (13-19 digits), SIN-like
///   `\d{3}[- ]?\d{3}[- ]?\d{3}` and phone numbers with `[REDACTED:<kind>]`
///
/// Kinds: `email`, `card`, `phone`, `sin`. Patterns are applied in that order
/// so a longer match (card, phone) wins over the 9-digit SIN pattern. All digit
/// patterns are bounded by "not a digit" look-arounds so a SIN pattern never
/// eats part of a longer number.
public struct Redactor {
    public enum Kind: String, CaseIterable {
        case email, card, phone, sin
    }

    private let rules: [(Kind, NSRegularExpression)]

    public init() {
        var rules: [(Kind, NSRegularExpression)] = []
        func add(_ kind: Kind, _ pattern: String) {
            // Patterns are constants; a failure here is a programming error.
            if let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) {
                rules.append((kind, re))
            } else {
                assertionFailure("Bad redaction pattern for \(kind)")
            }
        }
        add(.email, #"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}"#)
        // 13-19 digits, optionally grouped by single spaces or dashes.
        add(.card, #"(?<![0-9])[0-9](?:[ \-]?[0-9]){12,18}(?![0-9])"#)
        // North-American style: +1 (416) 555-1234, 416.555.1234, 4165551234, ...
        add(.phone, #"(?<![0-9])(?:\+?1[ .\-]?)?\(?[2-9][0-9]{2}\)?[ .\-]?[0-9]{3}[ .\-]?[0-9]{4}(?![0-9])"#)
        // International: + then 8-15 digits with common separators.
        add(.phone, #"(?<![0-9A-Z])\+[0-9](?:[ .\-()]?[0-9]){7,14}(?![0-9])"#)
        add(.sin, #"(?<![0-9])[0-9]{3}[\- ]?[0-9]{3}[\- ]?[0-9]{3}(?![0-9])"#)
        self.rules = rules
    }

    public static let shared = Redactor()

    public static func placeholder(_ kind: Kind) -> String {
        return "[REDACTED:\(kind.rawValue)]"
    }

    /// Returns the redacted string and the kinds that were found.
    public func redact(_ input: String) -> (text: String, kinds: [Kind]) {
        var text = input
        var found: [Kind] = []
        for (kind, re) in rules {
            let range = NSRange(text.startIndex..<text.endIndex, in: text)
            let count = re.numberOfMatches(in: text, options: [], range: range)
            if count > 0 {
                text = re.stringByReplacingMatches(in: text, options: [], range: range,
                                                   withTemplate: NSRegularExpression.escapedTemplate(for: Redactor.placeholder(kind)))
                if !found.contains(kind) { found.append(kind) }
            }
        }
        return (text, found)
    }

    public func redacted(_ input: String?) -> String? {
        guard let input = input else { return nil }
        return redact(input).text
    }

    public func containsPII(_ input: String) -> Bool {
        return !redact(input).kinds.isEmpty
    }

    /// Applies the redaction rules to every free-text field of an event.
    /// Typed text in a secure element is dropped entirely.
    public func redact(event: RecordedEvent) -> RecordedEvent {
        var e = event
        if let el = e.element, el.isSecure {
            e.text = nil
            e.element?.valueKind = .secure
        } else {
            e.text = redacted(e.text)
        }
        if var w = e.window {
            w.title = redacted(w.title)
            e.window = w
        }
        if var el = e.element {
            el.label = redacted(el.label)
            el.identifier = redacted(el.identifier)
            el.path = el.path.map { redact($0).text }
            e.element = el
        }
        if let url = e.url {
            e.url = redacted(URLSanitizer.strip(url))
        }
        return e
    }

    public func redact(node: UINode) -> UINode {
        return UINode(role: node.role, label: redacted(node.label), identifier: redacted(node.identifier),
                      path: node.path.map { redact($0).text })
    }

    public func redact(target: Target) -> Target {
        var t = target
        t.label = redacted(t.label)
        t.identifier = redacted(t.identifier)
        t.windowTitle = redacted(t.windowTitle)
        t.path = t.path.map { redact($0).text }
        return t
    }
}

public enum URLSanitizer {
    /// Strips query string, fragment and user-info; returns the input
    /// unchanged (minus anything after `?`/`#`) if it doesn't parse.
    public static func strip(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if var comps = URLComponents(string: trimmed), comps.scheme != nil {
            comps.query = nil
            comps.fragment = nil
            comps.user = nil
            comps.password = nil
            if let s = comps.string { return s }
        }
        var s = trimmed
        if let i = s.firstIndex(where: { $0 == "?" || $0 == "#" }) {
            s = String(s[s.startIndex..<i])
        }
        return s
    }

    /// Only http(s) URLs are ever opened during replay.
    public static func isSafeToOpen(_ raw: String) -> Bool {
        guard let comps = URLComponents(string: raw), let scheme = comps.scheme?.lowercased() else { return false }
        return (scheme == "https" || scheme == "http") && (comps.host?.isEmpty == false)
    }
}
