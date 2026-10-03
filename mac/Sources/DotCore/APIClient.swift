import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum APIError: Error, CustomStringConvertible, Equatable {
    /// Non-2xx with the contract error body (or a synthesized code).
    case http(status: Int, code: String, message: String)
    case network(String)
    case decoding(String)
    case notConfigured
    case notSignedIn

    public var description: String {
        switch self {
        case .http(let s, let c, let m): return "HTTP \(s) \(c): \(m)"
        case .network(let m): return "network: \(m)"
        case .decoding(let m): return "decoding: \(m)"
        case .notConfigured: return "server URL not configured"
        case .notSignedIn: return "not signed in"
        }
    }

    public var status: Int? {
        if case .http(let s, _, _) = self { return s }
        return nil
    }

    /// Worth retrying later (network trouble, 5xx, 408, 429).
    public var isTransient: Bool {
        switch self {
        case .network: return true
        case .http(let s, _, _): return s >= 500 || s == 408 || s == 429
        default: return false
        }
    }

    public var isAuthFailure: Bool {
        if case .http(let s, _, _) = self { return s == 401 }
        if case .notSignedIn = self { return true }
        return false
    }
}

/// Minimal transport so the client and uploader can be tested without a network.
public protocol HTTPTransport: AnyObject {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public final class URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init(timeout: TimeInterval = 30) {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = timeout
        cfg.timeoutIntervalForResource = timeout * 4
        cfg.httpCookieStorage = nil
        cfg.urlCache = nil
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: cfg)
    }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        // dataTask + continuation works on every OS version / platform we target.
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<(Data, HTTPURLResponse), Error>) in
            let task = session.dataTask(with: request) { data, response, error in
                if let error = error {
                    cont.resume(throwing: APIError.network(error.localizedDescription))
                    return
                }
                guard let http = response as? HTTPURLResponse else {
                    cont.resume(throwing: APIError.network("no HTTP response"))
                    return
                }
                cont.resume(returning: (data ?? Data(), http))
            }
            task.resume()
        }
    }
}

/// Typed client for the Work Shadower API (`/api/v1`), see docs/CONTRACT.md.
public final class APIClient {
    public var baseURL: URL?
    /// Returns the bearer token (read lazily, e.g. from the Keychain).
    public var tokenProvider: () -> String?
    public let transport: HTTPTransport

    public init(baseURL: URL?, transport: HTTPTransport = URLSessionTransport(), tokenProvider: @escaping () -> String?) {
        self.baseURL = baseURL
        self.transport = transport
        self.tokenProvider = tokenProvider
    }

    /// Accepts "https://host", "https://host/", or "https://host/api/v1".
    public static func normalizeBaseURL(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix("/api/v1") { s.removeLast("/api/v1".count) }
        guard let url = URL(string: s), let scheme = url.scheme?.lowercased(),
              scheme == "https" || scheme == "http", url.host != nil else { return nil }
        return url
    }

    // MARK: Request plumbing

    func makeURL(_ path: String, query: [URLQueryItem] = [], apiPrefix: Bool = true) throws -> URL {
        guard let base = baseURL else { throw APIError.notConfigured }
        let full = (apiPrefix ? "/api/v1" : "") + path
        guard var comps = URLComponents(url: base, resolvingAgainstBaseURL: false) else { throw APIError.notConfigured }
        let basePath = comps.path.hasSuffix("/") ? String(comps.path.dropLast()) : comps.path
        comps.path = basePath + full
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.notConfigured }
        return url
    }

    /// Resolves a possibly-relative URL returned by the server (e.g. presign).
    public func resolve(_ raw: String) -> URL? {
        if let u = URL(string: raw), u.scheme != nil { return u }
        guard let base = baseURL else { return nil }
        return URL(string: raw, relativeTo: base)?.absoluteURL
    }

    /// True when `url` points at our own API host (bearer may be attached).
    public func isSameOrigin(_ url: URL) -> Bool {
        guard let base = baseURL else { return false }
        return base.host?.lowercased() == url.host?.lowercased()
            && (base.scheme?.lowercased() ?? "") == (url.scheme?.lowercased() ?? "")
            && (base.port ?? defaultPort(base)) == (url.port ?? defaultPort(url))
    }

    private func defaultPort(_ u: URL) -> Int {
        return u.scheme?.lowercased() == "https" ? 443 : 80
    }

    func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil,
                 headers: [String: String] = [:], auth: Bool = true, apiPrefix: Bool = true) async throws -> Data {
        var req = URLRequest(url: try makeURL(path, query: query, apiPrefix: apiPrefix))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body = body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if auth {
            guard let token = tokenProvider(), !token.isEmpty else { throw APIError.notSignedIn }
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        let (data, resp) = try await transport.send(req)
        try APIClient.check(resp, data)
        return data
    }

    static func check(_ resp: HTTPURLResponse, _ data: Data) throws {
        guard (200..<300).contains(resp.statusCode) else {
            if let body = try? JSONDecoder().decode(APIErrorBody.self, from: data) {
                throw APIError.http(status: resp.statusCode, code: body.error.code, message: body.error.message ?? "")
            }
            throw APIError.http(status: resp.statusCode, code: "http_\(resp.statusCode)", message: "")
        }
    }

    func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try DotJSON.decoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: type))
        }
    }

    func encode<T: Encodable>(_ value: T) throws -> Data {
        return try DotJSON.encoder().encode(value)
    }

    // MARK: Auth & config

    public func healthz() async throws -> Bool {
        let data = try await request("GET", "/healthz", auth: false, apiPrefix: false)
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let ok = obj["ok"] as? Bool {
            return ok
        }
        return true
    }

    public func publicConfig() async throws -> PublicConfig {
        return try decode(PublicConfig.self, try await request("GET", "/config/public", auth: false))
    }

    public func devLogin(email: String, name: String, team: String?) async throws -> DevLoginResponse {
        let t = (team ?? "").trimmingCharacters(in: .whitespaces)
        let body = try encode(DevLoginRequest(email: email, name: name, team: t.isEmpty ? nil : t))
        return try decode(DevLoginResponse.self, try await request("POST", "/auth/dev-login", body: body, auth: false))
    }

    public func me() async throws -> User {
        return try decode(User.self, try await request("GET", "/me"))
    }

    public func config() async throws -> ServerConfig {
        return try decode(ServerConfig.self, try await request("GET", "/config"))
    }

    // MARK: Assets

    public func presign(_ req: PresignRequest) async throws -> PresignResponse {
        return try decode(PresignResponse.self, try await request("POST", "/assets/presign", body: try encode(req)))
    }

    /// Uploads bytes per a presign instruction. The bearer token is attached
    /// only when the URL is on our API origin (local storage driver); presigned
    /// S3 URLs must not carry extra auth.
    public func upload(_ instruction: UploadInstruction, data: Data, contentType: String) async throws {
        guard let url = resolve(instruction.url) else { throw APIError.decoding("upload url") }
        var req = URLRequest(url: url)
        req.httpMethod = instruction.method.isEmpty ? "PUT" : instruction.method.uppercased()
        req.httpBody = data
        var hasContentType = false
        for (k, v) in instruction.headers {
            req.setValue(v, forHTTPHeaderField: k)
            if k.lowercased() == "content-type" { hasContentType = true }
        }
        if !hasContentType { req.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        if isSameOrigin(url), let token = tokenProvider(), !token.isEmpty,
           !instruction.headers.keys.contains(where: { $0.lowercased() == "authorization" }) {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (body, resp) = try await transport.send(req)
        try APIClient.check(resp, body)
    }

    // MARK: Recordings

    public func createRecording(payload: Data, idempotencyKey: String) async throws -> RecordingCreated {
        let data = try await request("POST", "/recordings", body: payload, headers: ["Idempotency-Key": idempotencyKey])
        return try decode(RecordingCreated.self, data)
    }

    // MARK: Skills & search

    public func search(_ q: String, limit: Int = 10) async throws -> [SkillSummary] {
        let data = try await request("GET", "/search", query: [URLQueryItem(name: "q", value: q),
                                                               URLQueryItem(name: "limit", value: String(limit))])
        return try decode(SearchResponse.self, data).items
    }

    public func skill(id: String) async throws -> SkillDetail {
        return try decode(SkillDetail.self, try await request("GET", "/skills/\(pathEscape(id))"))
    }

    public func skillVersion(id: String, version: Int) async throws -> SkillVersion {
        return try decode(SkillVersion.self, try await request("GET", "/skills/\(pathEscape(id))/versions/\(version)"))
    }

    public func suggestFix(skillID: String, _ body: SuggestFixRequest) async throws {
        _ = try await request("POST", "/skills/\(pathEscape(skillID))/suggest-fix", body: try encode(body))
    }

    // MARK: Runs

    public func createRun(_ body: RunCreate, idempotencyKey: String) async throws -> RunCreated {
        let data = try await request("POST", "/runs", body: try encode(body), headers: ["Idempotency-Key": idempotencyKey])
        return try decode(RunCreated.self, data)
    }

    public func reportStep(runID: String, _ body: RunStepReport) async throws {
        _ = try await request("POST", "/runs/\(pathEscape(runID))/steps", body: try encode(body))
    }

    public func finishRun(runID: String, _ body: RunFinish) async throws {
        _ = try await request("POST", "/runs/\(pathEscape(runID))/finish", body: try encode(body))
    }

    public func repair(_ body: RepairRequest) async throws -> RepairResponse {
        return try decode(RepairResponse.self, try await request("POST", "/replay/repair", body: try encode(body)))
    }

    private func pathEscape(_ s: String) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-_.")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }
}
