#if os(macOS)
import Foundation
import CoreGraphics
import Security
import DotCore

/// Generic-password Keychain storage for the API token.
enum Keychain {
    static let service = "com.workshadower.dot.token"

    static func read(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func write(_ value: String, account: String) -> Bool {
        let data = Data(value.utf8)
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let update: [String: Any] = [kSecValueData as String: data]
        var status = SecItemUpdate(base as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = base
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(add as CFDictionary, nil)
        }
        if status != errSecSuccess {
            Log.app.error("keychain write failed: \(status, privacy: .public)")
        }
        return status == errSecSuccess
    }

    static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

/// Persistent app settings (UserDefaults) + token (Keychain, keyed by server host).
final class SettingsStore {
    private let d = UserDefaults.standard

    private enum K {
        static let serverURL = "serverURL"
        static let webURL = "webURL"
        static let deviceID = "deviceID"
        static let screenshotPolicy = "localScreenshotPolicy"
        static let userName = "userName"
        static let userEmail = "userEmail"
        static let cachedConfig = "cachedServerConfig"
        static let dotOrigin = "dotOrigin"
        static let onboarded = "onboarded"
        static let replayMode = "replayMode"
    }

    var serverURLString: String {
        get { return d.string(forKey: K.serverURL) ?? "" }
        set { d.set(newValue, forKey: K.serverURL) }
    }

    var serverURL: URL? { return APIClient.normalizeBaseURL(serverURLString) }

    /// Web app base URL; defaults to the server URL (SPA served by the same origin).
    var webURLString: String {
        get { return d.string(forKey: K.webURL) ?? "" }
        set { d.set(newValue, forKey: K.webURL) }
    }

    var webBaseURL: URL? {
        let w = webURLString.trimmingCharacters(in: .whitespaces)
        if !w.isEmpty, let u = URL(string: w.hasSuffix("/") ? String(w.dropLast()) : w), u.scheme != nil { return u }
        return serverURL
    }

    var deviceID: String {
        if let id = d.string(forKey: K.deviceID) { return id }
        let id = UUID().uuidString
        d.set(id, forKey: K.deviceID)
        return id
    }

    /// The user's own choice; the server policy can only make it stricter.
    var localScreenshotPolicy: ScreenshotPolicy {
        get { return ScreenshotPolicy(rawValue: d.string(forKey: K.screenshotPolicy) ?? "") ?? .keyMoments }
        set { d.set(newValue.rawValue, forKey: K.screenshotPolicy) }
    }

    var replayMode: RunMode {
        get { return RunMode(rawValue: d.string(forKey: K.replayMode) ?? "") ?? .guided }
        set { d.set(newValue.rawValue, forKey: K.replayMode) }
    }

    var userName: String {
        get { return d.string(forKey: K.userName) ?? "" }
        set { d.set(newValue, forKey: K.userName) }
    }

    var userEmail: String {
        get { return d.string(forKey: K.userEmail) ?? "" }
        set { d.set(newValue, forKey: K.userEmail) }
    }

    var onboarded: Bool {
        get { return d.bool(forKey: K.onboarded) }
        set { d.set(newValue, forKey: K.onboarded) }
    }

    var dotOrigin: CGPoint? {
        get {
            guard let a = d.array(forKey: K.dotOrigin) as? [Double], a.count == 2 else { return nil }
            return CGPoint(x: a[0], y: a[1])
        }
        set {
            if let p = newValue { d.set([Double(p.x), Double(p.y)], forKey: K.dotOrigin) } else { d.removeObject(forKey: K.dotOrigin) }
        }
    }

    var cachedConfig: ServerConfig? {
        get {
            guard let data = d.data(forKey: K.cachedConfig) else { return nil }
            return try? JSONDecoder().decode(ServerConfig.self, from: data)
        }
        set {
            if let c = newValue, let data = try? JSONEncoder().encode(c) { d.set(data, forKey: K.cachedConfig) }
        }
    }

    // MARK: token

    private var tokenAccount: String { return serverURL?.host ?? "default" }

    var token: String? {
        return Keychain.read(account: tokenAccount)
    }

    func setToken(_ token: String?) {
        if let t = token, !t.isEmpty {
            Keychain.write(t, account: tokenAccount)
        } else {
            Keychain.delete(account: tokenAccount)
        }
    }

    // MARK: paths

    static var supportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        let dir = base.appendingPathComponent("WorkShadower", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: nil)
        return dir
    }

    static var assetsDirectory: URL {
        let dir = supportDirectory.appendingPathComponent("assets", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: nil)
        return dir
    }

    static var queuePath: String {
        return supportDirectory.appendingPathComponent("queue.sqlite").path
    }

    static var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "\(v) (\(b))"
    }

    static var osVersion: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return "macOS \(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
    }

    static var bundleID: String {
        return Bundle.main.bundleIdentifier ?? "com.workshadower.dot"
    }
}
#endif
