#if os(macOS)
import AppKit
import ServiceManagement
import SwiftUI
import DotCore

/// Onboarding + settings: server, sign-in, permissions, privacy, launch at login.
final class SettingsModel: ObservableObject {
    @Published var serverURL = ""
    @Published var webURL = ""
    @Published var email = ""
    @Published var name = ""
    @Published var team = ""
    @Published var signedInAs: String?
    @Published var busy = false
    @Published var status: String?
    @Published var statusIsError = false
    @Published var authMode = "dev"
    @Published var permissions: [Permission: Bool] = [:]
    @Published var screenshotPolicy: ScreenshotPolicy = .keyMoments {
        didSet { settings.localScreenshotPolicy = screenshotPolicy }
    }
    @Published var replayMode: RunMode = .guided {
        didSet { settings.replayMode = replayMode }
    }
    @Published var launchAtLogin = false

    let settings: SettingsStore
    let api: APIClient
    var onSignedIn: () -> Void = {}
    var onServerChanged: () -> Void = {}
    private var timer: Timer?

    init(settings: SettingsStore, api: APIClient) {
        self.settings = settings
        self.api = api
        reload()
    }

    func reload() {
        serverURL = settings.serverURLString
        webURL = settings.webURLString
        email = settings.userEmail
        name = settings.userName
        screenshotPolicy = settings.localScreenshotPolicy
        replayMode = settings.replayMode
        signedInAs = settings.token == nil ? nil : (settings.userEmail.isEmpty ? "signed in" : settings.userEmail)
        launchAtLogin = SMAppService.mainApp.status == .enabled
        refreshPermissions()
    }

    func startPolling() {
        timer?.invalidate()
        let t = Timer(timeInterval: 1.5, repeats: true) { [weak self] _ in self?.refreshPermissions() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refreshPermissions() {
        var p: [Permission: Bool] = [:]
        for perm in Permission.allCases { p[perm] = perm.isGranted }
        if p != permissions { permissions = p }
    }

    func saveServer() {
        let trimmed = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = APIClient.normalizeBaseURL(trimmed) else {
            show("That doesn't look like a server URL (e.g. https://shadow.company.com).", error: true)
            return
        }
        settings.serverURLString = url.absoluteString
        settings.webURLString = webURL.trimmingCharacters(in: .whitespacesAndNewlines)
        api.baseURL = url
        serverURL = url.absoluteString
        signedInAs = settings.token == nil ? nil : settings.userEmail
        onServerChanged()
        busy = true
        Task {
            do {
                let ok = try await api.healthz()
                let pub = try await api.publicConfig()
                DispatchQueue.main.async {
                    self.busy = false
                    self.authMode = pub.authMode
                    self.show(ok ? "Connected." : "Server answered but isn't healthy.", error: !ok)
                }
            } catch {
                DispatchQueue.main.async {
                    self.busy = false
                    self.show("Can't reach the server: \(error)", error: true)
                }
            }
        }
    }

    func signIn() {
        let e = email.trimmingCharacters(in: .whitespacesAndNewlines)
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard e.contains("@"), !n.isEmpty else {
            show("Enter your work email and name.", error: true)
            return
        }
        guard api.baseURL != nil else {
            show("Save the server URL first.", error: true)
            return
        }
        let t = team.trimmingCharacters(in: .whitespacesAndNewlines)
        busy = true
        Task {
            do {
                let resp = try await api.devLogin(email: e, name: n, team: t.isEmpty ? nil : t)
                DispatchQueue.main.async {
                    self.settings.setToken(resp.token)
                    self.settings.userEmail = resp.user.email
                    self.settings.userName = resp.user.name
                    self.signedInAs = resp.user.email
                    self.busy = false
                    self.show("Signed in as \(resp.user.name).", error: false)
                    self.onSignedIn()
                }
            } catch {
                DispatchQueue.main.async {
                    self.busy = false
                    let api = error as? APIError
                    if api?.status == 404 || api?.status == 403 {
                        self.show("This server uses company SSO. Sign in on the web library; desktop SSO is coming next.", error: true)
                    } else {
                        self.show("Sign-in failed: \(error)", error: true)
                    }
                }
            }
        }
    }

    func signOut() {
        settings.setToken(nil)
        signedInAs = nil
        show("Signed out.", error: false)
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            show("Couldn't change login item: \(error.localizedDescription)", error: true)
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    private func show(_ text: String, error: Bool) {
        status = text
        statusIsError = error
    }
}

struct SettingsView: View {
    @ObservedObject var m: SettingsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Circle()
                    .fill(RadialGradient(colors: [Color(red: 0.55, green: 0.62, blue: 1.0),
                                                  Color(red: 0.24, green: 0.2, blue: 0.72)],
                                         center: UnitPoint(x: 0.35, y: 0.3), startRadius: 1, endRadius: 20))
                    .frame(width: 26, height: 26)
                VStack(alignment: .leading, spacing: 0) {
                    Text("Work Shadower").font(.system(size: 15, weight: .semibold))
                    Text("Record how you work. Share it. Let the dot do it.")
                        .font(.system(size: 11)).foregroundColor(.secondary)
                }
            }

            section("1 · Server") {
                TextField("https://shadow.yourcompany.com", text: $m.serverURL).textFieldStyle(.roundedBorder)
                TextField("Web library URL (optional, defaults to server)", text: $m.webURL).textFieldStyle(.roundedBorder)
                HStack {
                    Button("Save & test") { m.saveServer() }.disabled(m.busy)
                    Spacer()
                }
            }

            section("2 · Sign in") {
                if let who = m.signedInAs {
                    HStack {
                        Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
                        Text("Signed in as \(who)").font(.system(size: 12))
                        Spacer()
                        Button("Sign out") { m.signOut() }
                    }
                } else {
                    HStack {
                        TextField("Work email", text: $m.email).textFieldStyle(.roundedBorder)
                        TextField("Name", text: $m.name).textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        TextField("Team (optional, e.g. Claims)", text: $m.team).textFieldStyle(.roundedBorder)
                        Button("Sign in") { m.signIn() }.disabled(m.busy)
                    }
                }
            }

            section("3 · Permissions") {
                ForEach(Permission.allCases) { p in
                    HStack(alignment: .top) {
                        Image(systemName: (m.permissions[p] ?? false) ? "checkmark.circle.fill" : "circle")
                            .foregroundColor((m.permissions[p] ?? false) ? .green : .secondary)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(p.title + (p.required ? "" : " (optional)")).font(.system(size: 12, weight: .medium))
                            Text(p.why).font(.system(size: 10)).foregroundColor(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer()
                        if !(m.permissions[p] ?? false) {
                            Button("Allow…") {
                                p.request()
                                p.openSystemSettings()
                            }
                        }
                    }
                }
            }

            section("Privacy & behaviour") {
                Picker("Screenshots", selection: $m.screenshotPolicy) {
                    Text("Key moments only").tag(ScreenshotPolicy.keyMoments)
                    Text("Never").tag(ScreenshotPolicy.none)
                }
                Picker("When running skills", selection: $m.replayMode) {
                    Text("Guided (confirm each step)").tag(RunMode.guided)
                    Text("Auto (stop only when needed)").tag(RunMode.auto)
                }
                Toggle("Open at login", isOn: Binding(get: { m.launchAtLogin }, set: { m.setLaunchAtLogin($0) }))
                Text("Passwords are never recorded. Emails, card, SIN and phone numbers are blurred on this Mac before anything is uploaded.")
                    .font(.system(size: 10)).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let s = m.status {
                Text(s).font(.system(size: 11)).foregroundColor(m.statusIsError ? .red : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(20)
        .frame(width: 480)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.system(size: 10, weight: .semibold)).foregroundColor(.secondary)
            content()
        }
    }
}

/// Owns the settings window. Main thread only.
final class SettingsWindowController: NSObject, NSWindowDelegate {
    let model: SettingsModel
    private var window: NSWindow?

    init(model: SettingsModel) {
        self.model = model
        super.init()
    }

    func show() {
        model.reload()
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 600),
                             styleMask: [.titled, .closable], backing: .buffered, defer: false)
            w.title = "Work Shadower"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: SettingsView(m: model))
            w.delegate = self
            w.center()
            window = w
        }
        if let w = window, let host = w.contentView {
            w.setContentSize(host.fittingSize)
        }
        model.startPolling()
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        model.stopPolling()
    }
}
#endif
