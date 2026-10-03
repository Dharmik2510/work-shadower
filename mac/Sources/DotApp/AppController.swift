#if os(macOS)
import AppKit
import SwiftUI
import DotCore

/// Wires the dot, recorder, local queue/uploader, config + kill switch,
/// search, replay and settings together. Main thread only.
final class AppController: NSObject, NSApplicationDelegate {
    private let settings = SettingsStore()
    private lazy var api = APIClient(baseURL: settings.serverURL, tokenProvider: { [settings] in settings.token })

    private let dot = DotModel()
    private var dotPanel: DotPanel!
    private let recorder = Recorder()
    private var queue: UploadQueue?
    private var uploader: Uploader?
    private let network = NetworkMonitor()
    private let hotKey = HotKey()
    private lazy var replayer = Replayer(api: api, settings: settings)
    private let search = SearchPanelController()
    private lazy var settingsWindow = SettingsWindowController(model: SettingsModel(settings: settings, api: api))

    private var config: ServerConfig = .defaults
    private var configTimer: Timer?
    private var pendingURLs: [URL] = []
    private var launched = false

    static let dotSize: CGFloat = 64
    static let configRefresh: TimeInterval = 10 * 60

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        AX.configureTimeouts()
        config = settings.cachedConfig ?? .defaults

        setUpDot()
        setUpQueue()
        wireActions()

        network.start { [weak self] online in
            self?.uploader?.setOnline(online)
            if online { self?.refreshConfig() }
        }
        hotKey.register { [weak self] in self?.openSearch() }

        refreshConfig()
        let t = Timer(timeInterval: Self.configRefresh, repeats: true) { [weak self] _ in self?.refreshConfig() }
        RunLoop.main.add(t, forMode: .common)
        configTimer = t

        launched = true
        let urls = pendingURLs
        pendingURLs = []
        urls.forEach(handle(url:))

        if !settings.onboarded || settings.token == nil || settings.serverURL == nil || !Permission.allRequiredGranted {
            settings.onboarded = true
            settingsWindow.show()
        }
        uploader?.kick()
        Log.app.info("launched")
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        if !launched {
            pendingURLs.append(contentsOf: urls)
            return
        }
        urls.forEach(handle(url:))
    }

    private func setUpDot() {
        let size = Self.dotSize
        dotPanel = DotPanel(size: size)
        let host = FirstMouseHostingView(rootView: DotView(m: dot))
        host.frame = NSRect(x: 0, y: 0, width: size, height: size)
        dotPanel.contentView = host

        if let saved = settings.dotOrigin,
           NSScreen.screens.contains(where: { $0.visibleFrame.insetBy(dx: -8, dy: -8).contains(NSPoint(x: saved.x + size / 2, y: saved.y + size / 2)) }) {
            dotPanel.setFrameOrigin(saved)
        } else if let vf = NSScreen.main?.visibleFrame {
            dotPanel.setFrameOrigin(NSPoint(x: vf.maxX - size - 12, y: vf.maxY - size - 8))
        }
        dotPanel.orderFrontRegardless()
        dot.window = dotPanel
        dot.recordingEnabled = config.recordingEnabled
        dot.replayEnabled = config.replayEnabled
        dot.start()
    }

    private func setUpQueue() {
        do {
            let q = try UploadQueue(path: SettingsStore.queuePath)
            let u = Uploader(queue: q, api: api)
            u.onStatus = { [weak self] status in
                DispatchQueue.main.async { self?.apply(uploadStatus: status) }
            }
            queue = q
            uploader = u
            apply(uploadStatus: u.currentStatus)
        } catch {
            Log.upload.error("queue open failed: \(String(describing: error), privacy: .public)")
            dot.errorMessage = "Local storage problem — recordings can't be saved."
        }
    }

    private func wireActions() {
        dot.onClick = { [weak self] in self?.toggleRecording() }
        dot.onSearch = { [weak self] in self?.openSearch() }
        dot.onOpenLibrary = { [weak self] in self?.openWeb(path: "/") }
        dot.onRetryUploads = { [weak self] in self?.uploader?.retryNow() }
        dot.onSettings = { [weak self] in self?.settingsWindow.show() }
        dot.onMoved = { [weak self] origin in self?.settings.dotOrigin = origin }

        recorder.onAutoStop = { [weak self] reason in
            Log.recorder.info("auto stop: \(reason, privacy: .public)")
            self?.stopRecording()
        }

        search.model.api = api
        search.model.onLearn = { [weak self] s in
            self?.search.hide()
            self?.openWeb(path: "/skills/\(s.id)")
        }
        search.model.onRun = { [weak self] s in
            self?.search.hide()
            self?.startReplay(skillID: s.id, version: s.currentVersion)
        }

        replayer.dotFrame = { [weak self] in self?.dotPanel.frame }
        replayer.llmEnabled = { [weak self] in self?.config.llmEnabled ?? false }
        replayer.onStateChange = { [weak self] running in
            guard let self = self else { return }
            if running {
                self.dot.activity = .replaying
            } else if self.dot.activity == .replaying {
                self.dot.activity = .idle
            }
        }

        settingsWindow.model.onSignedIn = { [weak self] in
            self?.dot.errorMessage = nil
            self?.uploader?.signedIn()
            self?.refreshConfig()
        }
        settingsWindow.model.onServerChanged = { [weak self] in
            self?.refreshConfig()
        }
    }

    // MARK: Config + kill switch

    private func refreshConfig() {
        guard api.baseURL != nil, settings.token != nil else { return }
        Task {
            do {
                let c = try await api.config()
                DispatchQueue.main.async { self.apply(config: c) }
            } catch {
                Log.net.info("config fetch failed; keeping cached flags")
            }
        }
    }

    private func apply(config c: ServerConfig) {
        config = c
        settings.cachedConfig = c
        dot.recordingEnabled = c.recordingEnabled
        dot.replayEnabled = c.replayEnabled
        search.model.replayEnabled = c.replayEnabled
        if !c.recordingEnabled && recorder.isRecording {
            recorder.cancel()
            dot.activity = .idle
            dot.recordingStartedAt = nil
            flash("Recording was turned off by your admin. This recording was discarded.")
        }
    }

    // MARK: Recording

    private func toggleRecording() {
        if recorder.isRecording {
            stopRecording()
            return
        }
        guard dot.activity == .idle else { return }
        guard settings.serverURL != nil, settings.token != nil else {
            settingsWindow.show()
            return
        }
        guard config.recordingEnabled else {
            flash("Recording is turned off by your admin.")
            return
        }
        guard Permission.allRequiredGranted else {
            settingsWindow.show()
            return
        }
        let shots = config.screenshotPolicy == .keyMoments
            && settings.localScreenshotPolicy == .keyMoments
            && Permission.screenRecording.isGranted
        let opts = Recorder.Options(screenshots: shots,
                                    maxDuration: TimeInterval(max(1, config.maxRecordingMinutes) * 60))
        if recorder.start(options: opts) {
            dot.errorMessage = nil
            dot.activity = .recording
            dot.recordingStartedAt = Date()
        } else {
            flash("Couldn't start recording. Check Accessibility and Input Monitoring in Settings.")
            settingsWindow.show()
        }
    }

    private func stopRecording() {
        guard recorder.isRecording else { return }
        dot.activity = .idle
        dot.recordingStartedAt = nil
        recorder.stop { [weak self] result in
            DispatchQueue.main.async { self?.enqueue(result) }
        }
    }

    private func enqueue(_ result: RecordingResult?) {
        guard let r = result else { return }
        guard !r.events.isEmpty else {
            flash("Nothing was captured, so nothing was saved.")
            return
        }
        guard let queue = queue else { return }
        let payload = RecordingPayload(
            titleHint: r.titleHint, startedAt: r.startedAt, endedAt: r.endedAt,
            client: ClientInfo(appVersion: SettingsStore.appVersion, osVersion: SettingsStore.osVersion,
                               deviceID: settings.deviceID),
            events: r.events)
        do {
            let data = try DotJSON.encoder().encode(payload)
            let assets = r.shots.map { QueuedAsset(sha256: $0.sha256, path: $0.path, contentType: "image/jpeg", bytes: $0.bytes) }
            try queue.enqueueRecording(idempotencyKey: UUID().uuidString, payload: data, assets: assets)
            uploader?.kick()
        } catch {
            Log.upload.error("enqueue failed: \(String(describing: error), privacy: .public)")
            flash("Couldn't save the recording locally.")
        }
    }

    private func apply(uploadStatus s: UploaderStatus) {
        dot.pendingUploads = s.pending
        dot.uploading = s.isUploading
        dot.uploadProgress = s.progress
        if s.needsSignIn {
            dot.errorMessage = "Sign in again to upload recordings."
        } else if dot.errorMessage == "Sign in again to upload recordings." {
            dot.errorMessage = nil
        }
    }

    // MARK: Search / replay / links

    private func openSearch() {
        search.model.replayEnabled = config.replayEnabled
        search.toggle(near: dotPanel.frame)
    }

    private func startReplay(skillID: String, version: Int?) {
        guard config.replayEnabled else {
            flash("Running skills is turned off by your admin.")
            return
        }
        guard settings.token != nil else {
            settingsWindow.show()
            return
        }
        guard Permission.accessibility.isGranted else {
            flash("Allow Accessibility so I can press buttons for you.")
            settingsWindow.show()
            return
        }
        if recorder.isRecording {
            flash("Stop recording before running a skill.")
            return
        }
        replayer.start(skillID: skillID, version: version)
    }

    /// workshadower://run?skill_id=<uuid>&version=<n>
    private func handle(url: URL) {
        guard url.scheme == "workshadower" else { return }
        let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let items = parts?.queryItems ?? []
        let action = url.host ?? ""
        switch action {
        case "run":
            guard let id = items.first(where: { $0.name == "skill_id" })?.value,
                  UUID(uuidString: id) != nil else { return }
            let version = items.first(where: { $0.name == "version" })?.value.flatMap { Int($0) }
            startReplay(skillID: id, version: version)
        case "search":
            openSearch()
        default:
            break
        }
    }

    private func openWeb(path: String) {
        guard let base = settings.webBaseURL, let url = URL(string: base.absoluteString + path) else {
            settingsWindow.show()
            return
        }
        NSWorkspace.shared.open(url)
    }

    /// Shows a short message in the dot's tooltip/amber state for a few seconds.
    private func flash(_ message: String) {
        dot.errorMessage = message
        NSSound.beep()
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            if self?.dot.errorMessage == message { self?.dot.errorMessage = nil }
        }
    }
}
#endif
