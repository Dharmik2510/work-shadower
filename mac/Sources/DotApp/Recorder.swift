#if os(macOS)
import AppKit
import ApplicationServices
import Carbon.HIToolbox
import CoreGraphics
import DotCore

/// C callback for the listen-only event tap. Runs on the main run loop.
private func recorderTapCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent,
                                 refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    if let refcon = refcon {
        let recorder = Unmanaged<Recorder>.fromOpaque(refcon).takeUnretainedValue()
        recorder.handleTap(type: type, event: event)
    }
    return Unmanaged.passUnretained(event)
}

struct RecordingResult {
    let startedAt: Date
    let endedAt: Date
    let titleHint: String?
    let events: [RecordedEvent]
    let shots: [CapturedShot]
}

/// Captures structured accessibility events (not video).
///
/// Threading: every method runs on the main thread. The event tap source is
/// added to the main run loop, workspace notifications are delivered on the
/// main queue, and screenshot completions hop back to main.
final class Recorder {
    struct Options {
        var screenshots: Bool
        var maxDuration: TimeInterval
    }

    /// Called (main thread) when recording stops by itself: time cap or tap loss.
    var onAutoStop: ((String) -> Void)?

    private(set) var isRecording = false
    private(set) var startedAt: Date?

    private var options = Options(screenshots: false, maxDuration: 30 * 60)
    private var events: [RecordedEvent] = []
    private var seq = 0
    private var tap: CFMachPort?
    private var tapSource: CFRunLoopSource?
    private var workspaceObserver: NSObjectProtocol?
    private var pollTimer: Timer?
    private var capTimer: Timer?

    private var typing = TypingBuffer()
    private var focusCache: (key: String, info: ElementInfo, app: AppInfo, window: String?)?
    private var lastWindow: AXUIElement?
    private var lastWindowPID: pid_t = 0
    private var lastURL: String?
    private var cachedWebArea: (pid: pid_t, element: AXUIElement)?

    private var shots: [String: CapturedShot] = [:]
    private var newShotPaths: Set<String> = []
    private var pendingShots = 0

    private let screenshotter = Screenshotter()
    private let ourPID = ProcessInfo.processInfo.processIdentifier
    private let ourBundleID = SettingsStore.bundleID

    private static let reverseKeyCodes: [UInt16: String] = {
        var m: [UInt16: String] = [:]
        for (k, v) in KeyCodes.table where k.count == 1 { m[v] = k }
        return m
    }()

    // MARK: Lifecycle

    /// Returns false if the event tap could not be created (missing
    /// Accessibility / Input Monitoring permission).
    func start(options: Options) -> Bool {
        guard !isRecording else { return true }
        self.options = options
        events = []
        seq = 0
        shots = [:]
        newShotPaths = []
        typing = TypingBuffer()
        focusCache = nil
        lastWindow = nil
        lastURL = nil
        cachedWebArea = nil

        let mask: CGEventMask = (CGEventMask(1) << CGEventMask(CGEventType.leftMouseDown.rawValue))
            | (CGEventMask(1) << CGEventMask(CGEventType.rightMouseDown.rawValue))
            | (CGEventMask(1) << CGEventMask(CGEventType.keyDown.rawValue))
        guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                          eventsOfInterest: mask, callback: recorderTapCallback,
                                          userInfo: Unmanaged.passUnretained(self).toOpaque()) else {
            Log.recorder.error("event tap creation failed (permissions?)")
            return false
        }
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        self.tap = tap
        self.tapSource = source

        workspaceObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
            self?.appActivated(app)
        }

        let poll = Timer(timeInterval: 0.75, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(poll, forMode: .common)
        pollTimer = poll

        let cap = Timer(timeInterval: options.maxDuration, repeats: false) { [weak self] _ in
            self?.onAutoStop?("limit")
        }
        RunLoop.main.add(cap, forMode: .common)
        capTimer = cap

        isRecording = true
        startedAt = Date()
        Log.recorder.info("recording started")

        // Seed with the current frontmost app so the log has context.
        if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != ourPID {
            appActivated(front)
        }
        return true
    }

    /// Stops and returns the cleaned, redacted events. Waits briefly (≤1.5 s)
    /// for in-flight screenshots.
    func stop(completion: @escaping (RecordingResult?) -> Void) {
        guard isRecording, let started = startedAt else { completion(nil); return }
        flushTyping()
        teardown()
        let ended = Date()
        waitForShots(deadline: Date().addingTimeInterval(1.5)) { [weak self] in
            guard let self = self else { return }
            completion(self.buildResult(startedAt: started, endedAt: ended))
        }
    }

    /// Stops and discards everything (kill switch, user cancel).
    func cancel() {
        guard isRecording else { return }
        teardown()
        for p in newShotPaths { try? FileManager.default.removeItem(atPath: p) }
        events = []
        shots = [:]
        newShotPaths = []
        Log.recorder.info("recording discarded")
    }

    private func teardown() {
        if let tap = tap { CGEvent.tapEnable(tap: tap, enable: false) }
        if let src = tapSource { CFRunLoopRemoveSource(CFRunLoopGetMain(), src, .commonModes) }
        if let tap = tap { CFMachPortInvalidate(tap) }
        tap = nil
        tapSource = nil
        if let o = workspaceObserver { NSWorkspace.shared.notificationCenter.removeObserver(o) }
        workspaceObserver = nil
        pollTimer?.invalidate()
        pollTimer = nil
        capTimer?.invalidate()
        capTimer = nil
        isRecording = false
        startedAt = nil
    }

    private func waitForShots(deadline: Date, then: @escaping () -> Void) {
        if pendingShots <= 0 || Date() >= deadline {
            then()
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            self?.waitForShots(deadline: deadline, then: then)
        }
    }

    private func buildResult(startedAt: Date, endedAt: Date) -> RecordingResult {
        let cleaned = EventCleaner(ignoredBundleIDs: [ourBundleID]).clean(events)
        let referenced = Set(cleaned.compactMap { $0.screenshotSHA256 })
        var keep: [CapturedShot] = []
        for (hash, shot) in shots {
            if referenced.contains(hash) {
                keep.append(shot)
            } else if newShotPaths.contains(shot.path) {
                try? FileManager.default.removeItem(atPath: shot.path)
            }
        }
        var appNames: [String] = []
        for e in cleaned {
            if let n = e.app?.name, !appNames.contains(n) { appNames.append(n) }
            if appNames.count == 3 { break }
        }
        let hint = appNames.isEmpty ? nil : Redactor.shared.redact("Workflow in " + appNames.joined(separator: ", ")).text
        events = []
        shots = [:]
        newShotPaths = []
        Log.recorder.info("recording stopped: \(cleaned.count, privacy: .public) events, \(keep.count, privacy: .public) screenshots")
        return RecordingResult(startedAt: startedAt, endedAt: endedAt, titleHint: hint, events: cleaned, shots: keep)
    }

    // MARK: Event plumbing

    private func append(_ e: RecordedEvent) -> Int {
        var ev = e
        seq += 1
        ev.seq = seq
        events.append(ev)
        return seq
    }

    private func attachShot(toSeq s: Int, app pid: pid_t, window: AXUIElement?) {
        guard options.screenshots, screenshotter.isPermitted else { return }
        pendingShots += 1
        let frame = window.flatMap { AX.frame($0) }
        screenshotter.capture(pid: pid, preferredFrame: frame) { [weak self] shot in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.pendingShots -= 1
                guard let shot = shot else { return }
                let existed = self.shots[shot.sha256] != nil
                self.shots[shot.sha256] = shot
                if !existed { self.newShotPaths.insert(shot.path) }
                if let i = self.events.firstIndex(where: { $0.seq == s }) {
                    self.events[i].screenshotSHA256 = shot.sha256
                }
            }
        }
    }

    private func appInfo(pid: pid_t) -> AppInfo {
        let app = NSRunningApplication(processIdentifier: pid)
        return AppInfo(bundleID: app?.bundleIdentifier, name: app?.localizedName)
    }

    // MARK: Tap handling

    func handleTap(type: CGEventType, event: CGEvent) {
        switch type {
        case .tapDisabledByTimeout, .tapDisabledByUserInput:
            if let tap = tap { CGEvent.tapEnable(tap: tap, enable: true) }
            Log.recorder.notice("event tap re-enabled")
        case .leftMouseDown, .rightMouseDown:
            guard isRecording else { return }
            handleClick(at: event.location, right: type == .rightMouseDown)
        case .keyDown:
            guard isRecording else { return }
            handleKey(event)
        default:
            break
        }
    }

    private func handleClick(at point: CGPoint, right: Bool) {
        guard let el = AX.element(at: point), let pid = AX.pid(el) else { return }
        if pid == ourPID { return }                      // clicks on the Dot / our panels
        flushTyping()
        let info = AX.describe(el)
        let window = AX.element(el, "AXWindow")
        let title = window.flatMap { AX.string($0, "AXTitle") }
        let isMenu = info.role == "AXMenuItem" || info.role == "AXMenuBarItem"
        var ev = RecordedEvent(ts: Date(), type: isMenu ? .menu : .click, app: appInfo(pid: pid),
                               window: WindowInfo(title: title), element: info)
        if right { ev.key = "right_click" }
        let s = append(ev)
        if !right && KeyMoments.shouldScreenshotBeforeClick(role: info.role, label: info.label) {
            attachShot(toSeq: s, app: pid, window: window)
        }
    }

    private func handleKey(_ event: CGEvent) {
        if IsSecureEventInputEnabled() { return }       // a password field somewhere has secure input on
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == ourPID { return }

        let code = UInt16(truncatingIfNeeded: event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags
        let cmd = flags.contains(.maskCommand), ctrl = flags.contains(.maskControl)
        let alt = flags.contains(.maskAlternate), shift = flags.contains(.maskShift)

        // Identify the focused element (cached per element identity).
        guard let focused = AX.focusedElement(), let pid = AX.pid(focused), pid != ourPID else { return }
        let key = "\(pid)-\(CFHash(focused))"
        if focusCache?.key != key {
            let info = AX.describe(focused)
            let window = AX.element(focused, "AXWindow").flatMap { AX.string($0, "AXTitle") }
            // Flush what was typed into the previous element under the previous context.
            if let flushed = typing.focus(elementKey: key, element: info) {
                emitTyping(flushed)
            }
            focusCache = (key, info, appInfo(pid: pid), window)
        }
        guard let fc = focusCache else { return }
        let secure = fc.info.isSecure
        let named = KeyCodes.namedKey(for: code)

        if cmd || ctrl {
            // Shortcut: never record its characters as typed text.
            flushTyping()
            if secure { return }
            let base = named ?? Recorder.reverseKeyCodes[code]
                ?? NSEvent(cgEvent: event)?.charactersIgnoringModifiers?.lowercased() ?? ""
            guard !base.isEmpty else { return }
            let combo = KeyCombo(cmd: cmd, ctrl: ctrl, alt: alt, shift: shift, key: base)
            _ = append(RecordedEvent(ts: Date(), type: .key, app: fc.app, window: WindowInfo(title: fc.window),
                                     element: fc.info, key: combo.string))
            return
        }

        if let n = named {
            switch n {
            case "enter", "tab", "esc":
                flushTyping()
                _ = append(RecordedEvent(ts: Date(), type: .key, app: fc.app, window: WindowInfo(title: fc.window),
                                         element: fc.info, key: KeyCombo(shift: shift, key: n).string))
            case "delete":
                if !secure { typing.backspace() }
            case "space":
                if !secure { typing.append(" ", at: Date()) }
            default:
                break   // caret navigation / function keys: not meaningful on their own
            }
            return
        }

        if secure { return }   // belt and braces: TypingBuffer also refuses secure elements
        guard let chars = NSEvent(cgEvent: event)?.characters, !chars.isEmpty else { return }
        var printable = ""
        for scalar in chars.unicodeScalars where !CharacterSet.controlCharacters.contains(scalar) {
            printable.unicodeScalars.append(scalar)
        }
        guard !printable.isEmpty else { return }
        typing.append(printable, at: Date())
    }

    private func flushTyping() {
        if let flushed = typing.flush() { emitTyping(flushed) }
    }

    private func emitTyping(_ f: (text: String, element: ElementInfo?, startedAt: Date?)) {
        guard let el = f.element, !el.isSecure else { return }
        _ = append(RecordedEvent(ts: f.startedAt ?? Date(), type: .type, app: focusCache?.app,
                                 window: WindowInfo(title: focusCache?.window), element: el, text: f.text))
    }

    // MARK: App / window / URL changes

    private func appActivated(_ app: NSRunningApplication) {
        guard isRecording, app.processIdentifier != ourPID else { return }
        if let flushed = typing.reset() { emitTyping(flushed) }
        focusCache = nil
        let pid = app.processIdentifier
        if let b = app.bundleIdentifier, AX.chromiumBundleIDs.contains(b) {
            AX.enableManualAccessibility(pid: pid)
        }
        let window = AX.focusedWindow(pid: pid)
        let title = window.flatMap { AX.string($0, "AXTitle") }
        let s = append(RecordedEvent(ts: Date(), type: .appActivate,
                                     app: AppInfo(bundleID: app.bundleIdentifier, name: app.localizedName),
                                     window: WindowInfo(title: title)))
        lastWindow = window
        lastWindowPID = pid
        // Give the window a moment to draw before the key-moment screenshot.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            guard let self = self, self.isRecording else { return }
            self.attachShot(toSeq: s, app: pid, window: window)
        }
    }

    private func poll() {
        guard isRecording, let front = NSWorkspace.shared.frontmostApplication else { return }
        let pid = front.processIdentifier
        guard pid != ourPID else { return }
        guard let window = AX.focusedWindow(pid: pid) else { return }

        // New window / sheet / dialog in the same app.
        if pid == lastWindowPID, let last = lastWindow, !CFEqual(last, window) {
            flushTyping()
            let title = AX.string(window, "AXTitle")
            let s = append(RecordedEvent(ts: Date(), type: .windowOpen, app: appInfo(pid: pid), window: WindowInfo(title: title)))
            attachShot(toSeq: s, app: pid, window: window)
        }
        lastWindow = window
        lastWindowPID = pid

        // Browser URL (query strings are stripped by the cleaner + redactor).
        if let b = front.bundleIdentifier, AX.browserBundleIDs.contains(b), let url = currentURL(pid: pid, window: window) {
            let stripped = URLSanitizer.strip(url)
            if stripped != lastURL {
                lastURL = stripped
                _ = append(RecordedEvent(ts: Date(), type: .urlChange, app: appInfo(pid: pid),
                                         window: WindowInfo(title: AX.string(window, "AXTitle")), url: stripped))
            }
        }
    }

    private func currentURL(pid: pid_t, window: AXUIElement) -> String? {
        if let cached = cachedWebArea, cached.pid == pid, let u = AX.string(cached.element, "AXURL"), u.hasPrefix("http") {
            return u
        }
        cachedWebArea = nil
        if let doc = AX.string(window, "AXDocument"), doc.hasPrefix("http") { return doc }
        let nodes = AX.snapshot(root: window, maxNodes: 400, timeout: 0.3)
        if let web = nodes.first(where: { $0.ui.role == "AXWebArea" }) {
            cachedWebArea = (pid, web.element)
            if let u = AX.string(web.element, "AXURL"), u.hasPrefix("http") { return u }
        }
        return nil
    }
}
#endif
