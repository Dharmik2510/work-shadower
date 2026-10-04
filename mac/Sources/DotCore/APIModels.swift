import Foundation

// MARK: - Auth & config

/// The dot character a person picked (same five as the web app). Unknown values decode as `.orb`.
public enum AvatarKind: String, Codable, CaseIterable, Equatable {
    case orb, sprout, ember, nimbus, pixel

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? ""
        self = AvatarKind(rawValue: raw) ?? .orb
    }

    public var displayName: String {
        switch self {
        case .orb: return "Orb"
        case .sprout: return "Sprout"
        case .ember: return "Ember"
        case .nimbus: return "Nimbus"
        case .pixel: return "Pixel"
        }
    }
}

public struct User: Codable, Equatable {
    public var id: String
    public var email: String
    public var name: String
    public var role: String?
    public var teams: [TeamRef]
    public var avatar: AvatarKind

    public init(id: String, email: String, name: String, role: String? = nil, teams: [TeamRef] = [],
                avatar: AvatarKind = .orb) {
        self.id = id
        self.email = email
        self.name = name
        self.role = role
        self.teams = teams
        self.avatar = avatar
    }

    enum CodingKeys: String, CodingKey { case id, email, name, role, teams, avatar }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        email = (try? c.decodeIfPresent(String.self, forKey: .email)) ?? ""
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        role = try? c.decodeIfPresent(String.self, forKey: .role)
        teams = (try? c.decodeIfPresent([TeamRef].self, forKey: .teams)) ?? []
        avatar = (try? c.decodeIfPresent(AvatarKind.self, forKey: .avatar)) ?? .orb
    }
}

public struct DevLoginRequest: Codable, Equatable {
    public var email: String
    public var name: String
    public var team: String?

    public init(email: String, name: String, team: String?) {
        self.email = email
        self.name = name
        self.team = team
    }
}

public struct DevLoginResponse: Codable, Equatable {
    public var token: String
    public var user: User
}

public struct OIDCInfo: Codable, Equatable {
    public var issuer: String?
    public var clientID: String?

    enum CodingKeys: String, CodingKey {
        case issuer
        case clientID = "client_id"
    }
}

public struct PublicConfig: Codable, Equatable {
    public var authMode: String
    public var oidc: OIDCInfo?

    enum CodingKeys: String, CodingKey {
        case authMode = "auth_mode"
        case oidc
    }
}

public enum ScreenshotPolicy: String, Codable, Equatable, CaseIterable {
    case keyMoments = "key_moments"
    case none

    public init(from decoder: Decoder) throws {
        let s = try decoder.singleValueContainer().decode(String.self)
        // Unknown values fail closed (no screenshots).
        self = ScreenshotPolicy(rawValue: s) ?? .none
    }
}

/// `GET /config` feature flags.
public struct ServerConfig: Codable, Equatable {
    public var recordingEnabled: Bool
    public var replayEnabled: Bool
    public var llmEnabled: Bool
    public var maxRecordingMinutes: Int
    public var screenshotPolicy: ScreenshotPolicy

    public init(recordingEnabled: Bool = true, replayEnabled: Bool = true, llmEnabled: Bool = false,
                maxRecordingMinutes: Int = 30, screenshotPolicy: ScreenshotPolicy = .keyMoments) {
        self.recordingEnabled = recordingEnabled
        self.replayEnabled = replayEnabled
        self.llmEnabled = llmEnabled
        self.maxRecordingMinutes = maxRecordingMinutes
        self.screenshotPolicy = screenshotPolicy
    }

    /// Used before the first successful fetch (and no cached copy exists).
    public static let defaults = ServerConfig()

    enum CodingKeys: String, CodingKey {
        case recordingEnabled = "recording_enabled"
        case replayEnabled = "replay_enabled"
        case llmEnabled = "llm_enabled"
        case maxRecordingMinutes = "max_recording_minutes"
        case screenshotPolicy = "screenshot_policy"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Missing kill-switch flags fail closed.
        recordingEnabled = (try? c.decodeIfPresent(Bool.self, forKey: .recordingEnabled)) ?? false
        replayEnabled = (try? c.decodeIfPresent(Bool.self, forKey: .replayEnabled)) ?? false
        llmEnabled = (try? c.decodeIfPresent(Bool.self, forKey: .llmEnabled)) ?? false
        let minutes = (try? c.decodeIfPresent(Int.self, forKey: .maxRecordingMinutes)) ?? 30
        maxRecordingMinutes = max(1, min(minutes, 240))
        screenshotPolicy = (try? c.decodeIfPresent(ScreenshotPolicy.self, forKey: .screenshotPolicy)) ?? .none
    }
}

// MARK: - Assets

public struct PresignRequest: Codable, Equatable {
    public var sha256: String
    public var contentType: String
    public var bytes: Int

    public init(sha256: String, contentType: String, bytes: Int) {
        self.sha256 = sha256
        self.contentType = contentType
        self.bytes = bytes
    }

    enum CodingKeys: String, CodingKey {
        case sha256, bytes
        case contentType = "content_type"
    }
}

public struct UploadInstruction: Codable, Equatable {
    public var method: String
    public var url: String
    public var headers: [String: String]

    enum CodingKeys: String, CodingKey { case method, url, headers }

    public init(method: String, url: String, headers: [String: String] = [:]) {
        self.method = method
        self.url = url
        self.headers = headers
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        method = (try? c.decodeIfPresent(String.self, forKey: .method)) ?? "PUT"
        url = try c.decode(String.self, forKey: .url)
        headers = (try? c.decodeIfPresent([String: String].self, forKey: .headers)) ?? [:]
    }
}

public struct PresignResponse: Codable, Equatable {
    public var assetID: String
    public var exists: Bool
    public var upload: UploadInstruction?

    public init(assetID: String, exists: Bool, upload: UploadInstruction?) {
        self.assetID = assetID
        self.exists = exists
        self.upload = upload
    }

    enum CodingKeys: String, CodingKey {
        case exists, upload
        case assetID = "asset_id"
    }
}

// MARK: - Recordings

public struct RecordingCreated: Codable, Equatable {
    public var id: String
    public var status: String?
}

// MARK: - Search

public struct SearchResponse: Codable, Equatable {
    public var items: [SkillSummary]
}

// MARK: - Runs

public enum RunMode: String, Codable, Equatable, CaseIterable {
    case guided
    case auto
}

public struct RunCreate: Codable, Equatable {
    public var skillID: String
    public var version: Int
    public var mode: RunMode
    public var inputs: [String: String]

    public init(skillID: String, version: Int, mode: RunMode, inputs: [String: String]) {
        self.skillID = skillID
        self.version = version
        self.mode = mode
        self.inputs = inputs
    }

    enum CodingKeys: String, CodingKey {
        case version, mode, inputs
        case skillID = "skill_id"
    }
}

public struct RunCreated: Codable, Equatable {
    public var id: String
}

public enum StepStatus: String, Codable, Equatable {
    case ok, repaired, failed, skipped, confirmed
}

public enum StepStrategy: String, Codable, Equatable {
    case deterministic
    case llmRepair = "llm_repair"
    case vision
    case human
}

public struct RunStepReport: Codable, Equatable {
    public var stepIndex: Int
    public var status: StepStatus
    public var strategy: StepStrategy
    public var durationMS: Int
    public var detail: String?

    public init(stepIndex: Int, status: StepStatus, strategy: StepStrategy, durationMS: Int, detail: String? = nil) {
        self.stepIndex = stepIndex
        self.status = status
        self.strategy = strategy
        self.durationMS = durationMS
        self.detail = detail
    }

    enum CodingKeys: String, CodingKey {
        case status, strategy, detail
        case stepIndex = "step_index"
        case durationMS = "duration_ms"
    }
}

public enum RunOutcome: String, Codable, Equatable {
    case succeeded, failed, aborted
}

public struct RunFinish: Codable, Equatable {
    public var status: RunOutcome
    public var error: String?

    public init(status: RunOutcome, error: String? = nil) {
        self.status = status
        self.error = error
    }
}

/// A compact UI node, used both for local target matching and for
/// `POST /replay/repair` `ui_tree`.
public struct UINode: Codable, Equatable {
    public var role: String?
    public var label: String?
    public var identifier: String?
    public var path: [String]

    public init(role: String?, label: String?, identifier: String?, path: [String]) {
        self.role = role
        self.label = label
        self.identifier = identifier
        self.path = path
    }

    enum CodingKeys: String, CodingKey { case role, label, identifier, path }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(role, forKey: .role)
        try c.encode(label, forKey: .label)
        try c.encode(identifier, forKey: .identifier)
        try c.encode(path, forKey: .path)
    }
}

public struct RepairRequest: Codable, Equatable {
    public var skillID: String
    public var version: Int
    public var stepIndex: Int
    public var uiTree: [UINode]

    public init(skillID: String, version: Int, stepIndex: Int, uiTree: [UINode]) {
        self.skillID = skillID
        self.version = version
        self.stepIndex = stepIndex
        self.uiTree = Array(uiTree.prefix(300))
    }

    enum CodingKeys: String, CodingKey {
        case version
        case skillID = "skill_id"
        case stepIndex = "step_index"
        case uiTree = "ui_tree"
    }
}

public struct RepairResponse: Codable, Equatable {
    public var target: Target?
    public var confidence: Double

    public init(target: Target?, confidence: Double) {
        self.target = target
        self.confidence = confidence
    }

    enum CodingKeys: String, CodingKey { case target, confidence }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        target = try? c.decodeIfPresent(Target.self, forKey: .target)
        confidence = (try? c.decodeIfPresent(Double.self, forKey: .confidence)) ?? 0
    }
}

public struct SuggestFixRequest: Codable, Equatable {
    public var stepIndex: Int
    public var newTarget: Target
    public var note: String

    public init(stepIndex: Int, newTarget: Target, note: String) {
        self.stepIndex = stepIndex
        self.newTarget = newTarget
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case note
        case stepIndex = "step_index"
        case newTarget = "new_target"
    }
}

public struct APIErrorBody: Codable, Equatable {
    public struct Inner: Codable, Equatable {
        public var code: String
        public var message: String?
    }
    public var error: Inner
}
