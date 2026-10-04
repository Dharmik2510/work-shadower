import Foundation

// MARK: - Skill content (skill_versions.content), decoded leniently: content is
// produced by an LLM or edited by people, so every field is optional on input.

public struct SkillInput: Codable, Equatable {
    public var name: String
    public var description: String?
    public var example: String?

    public init(name: String, description: String? = nil, example: String? = nil) {
        self.name = name
        self.description = description
        self.example = example
    }
}

/// UI target of a step. Also used as `new_target` in suggest-fix and as the
/// target returned by `/replay/repair`.
public struct Target: Codable, Equatable {
    public var role: String?
    public var label: String?
    public var identifier: String?
    public var path: [String]
    public var windowTitle: String?

    public init(role: String? = nil, label: String? = nil, identifier: String? = nil,
                path: [String] = [], windowTitle: String? = nil) {
        self.role = role
        self.label = label
        self.identifier = identifier
        self.path = path
        self.windowTitle = windowTitle
    }

    enum CodingKeys: String, CodingKey {
        case role, label, identifier, path
        case windowTitle = "window_title"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = try? c.decodeIfPresent(String.self, forKey: .role)
        label = try? c.decodeIfPresent(String.self, forKey: .label)
        identifier = try? c.decodeIfPresent(String.self, forKey: .identifier)
        path = (try? c.decodeIfPresent([String].self, forKey: .path)) ?? []
        windowTitle = try? c.decodeIfPresent(String.self, forKey: .windowTitle)
    }

    public func encode(to encoder: Encoder) throws {
        // Explicit nulls keep the shape identical to the contract example.
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(role, forKey: .role)
        try c.encode(label, forKey: .label)
        try c.encode(identifier, forKey: .identifier)
        try c.encode(path, forKey: .path)
        try c.encode(windowTitle, forKey: .windowTitle)
    }

    public var isEmpty: Bool {
        return (role ?? "").isEmpty && (label ?? "").isEmpty && (identifier ?? "").isEmpty && path.isEmpty
    }
}

public enum ActionType: String, Codable, Equatable {
    case openApp = "open_app"
    case openURL = "open_url"
    case click
    case type
    case key
    case menu
    case wait
    case unknown

    public init(from decoder: Decoder) throws {
        let s = try decoder.singleValueContainer().decode(String.self)
        self = ActionType(rawValue: s) ?? .unknown
    }
}

public struct StepAction: Codable, Equatable {
    public var type: ActionType
    public var target: Target?
    public var text: String?
    public var key: String?
    public var url: String?

    public init(type: ActionType, target: Target? = nil, text: String? = nil, key: String? = nil, url: String? = nil) {
        self.type = type
        self.target = target
        self.text = text
        self.key = key
        self.url = url
    }

    enum CodingKeys: String, CodingKey { case type, target, text, key, url }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decode(ActionType.self, forKey: .type)) ?? .unknown
        target = try? c.decodeIfPresent(Target.self, forKey: .target)
        text = try? c.decodeIfPresent(String.self, forKey: .text)
        key = try? c.decodeIfPresent(String.self, forKey: .key)
        url = try? c.decodeIfPresent(String.self, forKey: .url)
    }
}

public struct ElementExpectation: Codable, Equatable {
    public var role: String?
    public var label: String?

    public init(role: String? = nil, label: String? = nil) {
        self.role = role
        self.label = label
    }
}

public struct StepExpect: Codable, Equatable {
    public var windowTitleContains: String?
    public var elementPresent: ElementExpectation?

    public init(windowTitleContains: String? = nil, elementPresent: ElementExpectation? = nil) {
        self.windowTitleContains = windowTitleContains
        self.elementPresent = elementPresent
    }

    enum CodingKeys: String, CodingKey {
        case windowTitleContains = "window_title_contains"
        case elementPresent = "element_present"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        windowTitleContains = try? c.decodeIfPresent(String.self, forKey: .windowTitleContains)
        elementPresent = try? c.decodeIfPresent(ElementExpectation.self, forKey: .elementPresent)
    }

    public var isEmpty: Bool {
        return (windowTitleContains ?? "").isEmpty && elementPresent == nil
    }
}

public struct SkillStep: Codable, Equatable {
    public var index: Int
    public var title: String
    public var instruction: String
    public var app: String?
    public var action: StepAction?
    public var expect: StepExpect?
    public var screenshotSHA256: String?
    public var irreversible: Bool
    /// Left out of the skill by the relevance filter or a reviewer. Published skills never contain
    /// excluded steps, but drafts can; replay always skips them.
    public var excluded: Bool

    public init(index: Int, title: String, instruction: String, app: String? = nil, action: StepAction? = nil,
                expect: StepExpect? = nil, screenshotSHA256: String? = nil, irreversible: Bool = false,
                excluded: Bool = false) {
        self.index = index
        self.title = title
        self.instruction = instruction
        self.app = app
        self.action = action
        self.expect = expect
        self.screenshotSHA256 = screenshotSHA256
        self.irreversible = irreversible
        self.excluded = excluded
    }

    enum CodingKeys: String, CodingKey {
        case index, title, instruction, app, action, expect, irreversible, excluded
        case screenshotSHA256 = "screenshot_sha256"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        index = (try? c.decodeIfPresent(Int.self, forKey: .index)) ?? 0
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        instruction = (try? c.decodeIfPresent(String.self, forKey: .instruction)) ?? ""
        app = try? c.decodeIfPresent(String.self, forKey: .app)
        action = try? c.decodeIfPresent(StepAction.self, forKey: .action)
        expect = try? c.decodeIfPresent(StepExpect.self, forKey: .expect)
        screenshotSHA256 = try? c.decodeIfPresent(String.self, forKey: .screenshotSHA256)
        irreversible = (try? c.decodeIfPresent(Bool.self, forKey: .irreversible)) ?? false
        excluded = (try? c.decodeIfPresent(Bool.self, forKey: .excluded)) ?? false
    }
}

public struct SkillContent: Codable, Equatable {
    public var title: String
    public var goal: String
    public var apps: [String]
    public var prerequisites: [String]
    public var inputs: [SkillInput]
    public var steps: [SkillStep]
    public var tags: [String]

    /// The steps replay performs: excluded steps are skipped.
    public var runnableSteps: [SkillStep] { return steps.filter { !$0.excluded } }

    public init(title: String, goal: String = "", apps: [String] = [], prerequisites: [String] = [],
                inputs: [SkillInput] = [], steps: [SkillStep] = [], tags: [String] = []) {
        self.title = title
        self.goal = goal
        self.apps = apps
        self.prerequisites = prerequisites
        self.inputs = inputs
        self.steps = steps
        self.tags = tags
    }

    enum CodingKeys: String, CodingKey { case title, goal, apps, prerequisites, inputs, steps, tags }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? "Untitled skill"
        goal = (try? c.decodeIfPresent(String.self, forKey: .goal)) ?? ""
        apps = (try? c.decodeIfPresent([String].self, forKey: .apps)) ?? []
        prerequisites = (try? c.decodeIfPresent([String].self, forKey: .prerequisites)) ?? []
        inputs = (try? c.decodeIfPresent([SkillInput].self, forKey: .inputs)) ?? []
        steps = (try? c.decodeIfPresent([SkillStep].self, forKey: .steps)) ?? []
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        // Normalize indexes so steps are always 1..N in order.
        steps.sort { $0.index < $1.index }
        for i in steps.indices where steps[i].index <= 0 {
            steps[i].index = i + 1
        }
    }
}

// MARK: - API shapes around skills

public struct PersonRef: Codable, Equatable {
    public var id: String?
    public var name: String?
    public var email: String?
}

public struct TeamRef: Codable, Equatable {
    public var id: String
    public var name: String
}

public struct SkillHealth: Codable, Equatable {
    public var runs: Int?
    public var successRate: Double?
    public var lastRunAt: String?

    enum CodingKeys: String, CodingKey {
        case runs
        case successRate = "success_rate"
        case lastRunAt = "last_run_at"
    }
}

/// `SkillSummary` (+ optional `score` in search results).
public struct SkillSummary: Codable, Equatable, Identifiable {
    public var id: String
    public var title: String
    public var goal: String
    public var owner: PersonRef?
    public var team: TeamRef?
    public var visibility: String?
    public var status: String?
    public var tags: [String]
    public var apps: [String]
    public var currentVersion: Int?
    public var updatedAt: String?
    public var health: SkillHealth?
    public var score: Double?

    public init(id: String, title: String, goal: String = "", owner: PersonRef? = nil, team: TeamRef? = nil,
                visibility: String? = nil, status: String? = nil, tags: [String] = [], apps: [String] = [],
                currentVersion: Int? = nil, updatedAt: String? = nil, health: SkillHealth? = nil, score: Double? = nil) {
        self.id = id
        self.title = title
        self.goal = goal
        self.owner = owner
        self.team = team
        self.visibility = visibility
        self.status = status
        self.tags = tags
        self.apps = apps
        self.currentVersion = currentVersion
        self.updatedAt = updatedAt
        self.health = health
        self.score = score
    }

    enum CodingKeys: String, CodingKey {
        case id, title, goal, owner, team, visibility, status, tags, apps, health, score
        case currentVersion = "current_version"
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? "Untitled skill"
        goal = (try? c.decodeIfPresent(String.self, forKey: .goal)) ?? ""
        owner = try? c.decodeIfPresent(PersonRef.self, forKey: .owner)
        team = try? c.decodeIfPresent(TeamRef.self, forKey: .team)
        visibility = try? c.decodeIfPresent(String.self, forKey: .visibility)
        status = try? c.decodeIfPresent(String.self, forKey: .status)
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        apps = (try? c.decodeIfPresent([String].self, forKey: .apps)) ?? []
        currentVersion = try? c.decodeIfPresent(Int.self, forKey: .currentVersion)
        updatedAt = try? c.decodeIfPresent(String.self, forKey: .updatedAt)
        health = try? c.decodeIfPresent(SkillHealth.self, forKey: .health)
        score = try? c.decodeIfPresent(Double.self, forKey: .score)
    }
}

/// Full `Skill` response; only the fields Dot needs.
public struct SkillDetail: Codable, Equatable {
    public var id: String
    public var status: String?
    public var currentVersion: Int?
    public var published: SkillContent?
    public var draft: SkillContent?

    enum CodingKeys: String, CodingKey {
        case id, status, published, draft
        case currentVersion = "current_version"
    }
}

/// `GET /skills/{id}/versions/{n}`.
public struct SkillVersion: Codable, Equatable {
    public var version: Int
    public var content: SkillContent
    public var createdAt: String?

    enum CodingKeys: String, CodingKey {
        case version, content
        case createdAt = "created_at"
    }
}
