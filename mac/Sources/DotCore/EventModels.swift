import Foundation

// MARK: - Recorded events (Mac -> server), see docs/CONTRACT.md "Recorded event"

public enum EventType: String, Codable, Equatable {
    case appActivate = "app_activate"
    case click
    case type
    case key
    case menu
    case windowOpen = "window_open"
    case urlChange = "url_change"
    case scroll
}

public enum ValueKind: String, Codable, Equatable {
    case text
    case secure
    case none
}

public struct AppInfo: Codable, Equatable {
    public var bundleID: String?
    public var name: String?

    public init(bundleID: String?, name: String?) {
        self.bundleID = bundleID
        self.name = name
    }

    enum CodingKeys: String, CodingKey {
        case bundleID = "bundle_id"
        case name
    }
}

public struct WindowInfo: Codable, Equatable {
    public var title: String?

    public init(title: String?) { self.title = title }
}

public struct ElementInfo: Codable, Equatable {
    public var role: String?
    public var subrole: String?
    public var label: String?
    public var identifier: String?
    public var path: [String]
    public var valueKind: ValueKind

    public init(role: String?, subrole: String? = nil, label: String? = nil,
                identifier: String? = nil, path: [String] = [], valueKind: ValueKind = .none) {
        self.role = role
        self.subrole = subrole
        self.label = label
        self.identifier = identifier
        self.path = path
        self.valueKind = valueKind
    }

    enum CodingKeys: String, CodingKey {
        case role, subrole, label, identifier, path
        case valueKind = "value_kind"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = try c.decodeIfPresent(String.self, forKey: .role)
        subrole = try c.decodeIfPresent(String.self, forKey: .subrole)
        label = try c.decodeIfPresent(String.self, forKey: .label)
        identifier = try c.decodeIfPresent(String.self, forKey: .identifier)
        path = (try c.decodeIfPresent([String].self, forKey: .path)) ?? []
        valueKind = (try? c.decodeIfPresent(ValueKind.self, forKey: .valueKind)) ?? ValueKind.none
    }

    /// True for the secure-text-field subrole (passwords etc.).
    public var isSecure: Bool {
        return valueKind == .secure || subrole == "AXSecureTextField"
    }
}

public struct RecordedEvent: Codable, Equatable {
    public var seq: Int
    public var ts: Date
    public var type: EventType
    public var app: AppInfo?
    public var window: WindowInfo?
    public var element: ElementInfo?
    public var text: String?
    public var key: String?
    public var url: String?
    public var screenshotSHA256: String?

    public init(seq: Int = 0, ts: Date, type: EventType, app: AppInfo? = nil, window: WindowInfo? = nil,
                element: ElementInfo? = nil, text: String? = nil, key: String? = nil,
                url: String? = nil, screenshotSHA256: String? = nil) {
        self.seq = seq
        self.ts = ts
        self.type = type
        self.app = app
        self.window = window
        self.element = element
        self.text = text
        self.key = key
        self.url = url
        self.screenshotSHA256 = screenshotSHA256
    }

    enum CodingKeys: String, CodingKey {
        case seq, ts, type, app, window, element, text, key, url
        case screenshotSHA256 = "screenshot_sha256"
    }
}

public struct ClientInfo: Codable, Equatable {
    public var appVersion: String
    public var osVersion: String
    public var deviceID: String

    public init(appVersion: String, osVersion: String, deviceID: String) {
        self.appVersion = appVersion
        self.osVersion = osVersion
        self.deviceID = deviceID
    }

    enum CodingKeys: String, CodingKey {
        case appVersion = "app_version"
        case osVersion = "os_version"
        case deviceID = "device_id"
    }
}

/// Body of `POST /recordings`.
public struct RecordingPayload: Codable, Equatable {
    public var titleHint: String?
    /// The author's answer to "What did you just do?" (redacted on device). Optional.
    public var intent: String?
    public var startedAt: Date
    public var endedAt: Date
    public var client: ClientInfo
    public var events: [RecordedEvent]

    public init(titleHint: String?, startedAt: Date, endedAt: Date, client: ClientInfo, events: [RecordedEvent],
                intent: String? = nil) {
        self.titleHint = titleHint
        self.intent = intent
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.client = client
        self.events = events
    }

    enum CodingKeys: String, CodingKey {
        case titleHint = "title_hint"
        case intent
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case client, events
    }

    /// Normalises and redacts a typed intent; nil when empty. Capped at 1000 characters (server limit).
    public static func cleanIntent(_ raw: String?) -> String? {
        let collapsed = (raw ?? "").split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !collapsed.isEmpty else { return nil }
        return String(Redactor.shared.redact(String(collapsed.prefix(1000))).text.prefix(1000))
    }

    /// Screenshot hashes referenced by events (deduplicated, in order).
    public var referencedScreenshots: [String] {
        var seen = Set<String>()
        var out: [String] = []
        for e in events {
            if let h = e.screenshotSHA256, !seen.contains(h) {
                seen.insert(h)
                out.append(h)
            }
        }
        return out
    }
}
