#if os(macOS)
// Work Shadower in the notch: the panel, its states, hover intent, clicks and keys.
import AppKit
import SwiftUI
import DotCore

enum ToastTone: Equatable { case good, info, attention }

/// Everything the notch view shows that isn't already on DotModel / SearchModel / ReplayHUDModel.
final class NotchModel: ObservableObject {
    @Published var mode: NotchMode = .idle
    @Published var metrics = NotchMetrics(screenFrame: .zero, hasNotch: true, notchSize: CGSize(width: 200, height: 32),
                                          menuBarHeight: 32, menuBarHidden: false)
    /// Pointer is on the notch (drives the avatar waking and smiling).
    @Published var hovering = false
    /// Hidden entirely (no-notch display while a full-screen app is in front and nothing is happening).
    @Published var suppressed = false
    @Published var firstName = ""
    /// Bumped to move keyboard focus into the search field.
    @Published var focusSearchToken = 0
    @Published var selectedResult = 0

    // toast
    @Published var toastTitle = ""
    @Published var toastDetail = ""
    @Published var toastTone: ToastTone = .good
    @Published var toastActionTitle: String?
    var toastAction: (() -> Void)?

    // intent ("What did you just do?")
    @Published var intentText = ""
    @Published var intentSecondsLeft = 0
    @Published var intentSteps = 0
    @Published var intentAppHint = ""
    @Published var focusIntentToken = 0

    // actions (wired by AppController)
    var onRecordToggle: () -> Void = {}
    var onDiscardRecording: () -> Void = {}
    var onLibrary: () -> Void = {}
    var onRecordings: () -> Void = {}
    var onSettings: () -> Void = {}
    var onChooseAvatar: (AvatarKind) -> Void = { _ in }
    var onUseFloatingDot: () -> Void = {}
    var onShowReplayHUD: () -> Void = {}

    /// Set by the controller.
    var requestMode: (NotchMode) -> Void = { _ in }
    var finishIntent: (String?) -> Void = { _ in }
}

/// Owns the notch panel. Main thread only.
final class NotchController: NSObject {
    let model = NotchModel()
    private var panel: NotchPanel?
    private weak var dot: DotModel?
    private weak var search: SearchModel?
    private weak var hud: ReplayHUDModel?

    private var timer: Timer?
    private var hoverSince: Date?
    private var awaySince: Date?
    private var globalClick: Any?
    private var localKeys: Any?
    private var screenObserver: NSObjectProtocol?
    private var toastTimer: Timer?
    private var intentTimer: Timer?
    private var intentCompletion: ((String?) -> Void)?

    /// Hover intent: how long the pointer rests before the notch peeks, and how long it may
    /// wander off before it folds away again.
    static let peekDelay: TimeInterval = 0.12
    static let peekGrace: TimeInterval = 0.35
    static let openGrace: TimeInterval = 0.9

    var isVisible: Bool { panel?.isVisible ?? false }
    var isHovering: Bool { model.hovering }

    // MARK: Lifecycle

    func attach(dot: DotModel, search: SearchModel, hud: ReplayHUDModel) {
        self.dot = dot
        self.search = search
        self.hud = hud
        model.requestMode = { [weak self] m in self?.set(m) }
        model.finishIntent = { [weak self] text in self?.endIntent(text) }
    }

    func show() {
        if panel == nil { build() }
        relayout()
        panel?.orderFrontRegardless()
        startTracking()
    }

    func hide() {
        stopTracking()
        panel?.orderOut(nil)
        set(.idle)
    }

    private func build() {
        guard let dot = dot, let search = search, let hud = hud else { return }
        let p = NotchPanel(size: NotchLayout.canvas)
        let root = NotchRootView(model: model, dot: dot, search: search, hud: hud)
        let host = FirstMouseHostingView(rootView: root)
        host.frame = NSRect(origin: .zero, size: NotchLayout.canvas)
        p.contentView = host
        panel = p
        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.relayout() }
    }

    /// Pins the canvas to the top centre of the notch's screen.
    private func relayout() {
        guard let p = panel, let screen = NotchMetrics.preferredScreen() else { return }
        let m = NotchMetrics.measure(screen)
        if m != model.metrics { model.metrics = m }
        let c = NotchLayout.canvas
        p.setFrame(NSRect(x: m.screenFrame.midX - c.width / 2, y: m.screenFrame.maxY - c.height,
                          width: c.width, height: c.height), display: true)
    }

    // MARK: Geometry

    /// Status ears are shown while something is going on.
    var hasStatus: Bool {
        guard let d = dot else { return false }
        return d.activity != .idle || d.uploading || d.errorMessage != nil
    }

    /// The black shape's rect in screen coordinates (AppKit, origin bottom-left).
    var shapeFrame: NSRect {
        let m = model.metrics
        let s = NotchLayout.size(model.mode, metrics: m, hasStatus: hasStatus)
        return NSRect(x: m.screenFrame.midX - s.width / 2, y: m.screenFrame.maxY - s.height,
                      width: s.width, height: s.height)
    }

    /// Where the pointer counts as "on the notch". On displays without a notch the idle lip is
    /// tiny, so the hot zone is the stand-in notch area instead.
    private var hotFrame: NSRect {
        let m = model.metrics
        if model.mode == .idle && !hasStatus {
            let n = m.notchSize
            return NSRect(x: m.screenFrame.midX - n.width / 2, y: m.screenFrame.maxY - n.height,
                          width: n.width, height: n.height).insetBy(dx: -4, dy: -2)
        }
        return shapeFrame.insetBy(dx: -4, dy: -4)
    }

    /// Centre of the avatar, so its pupils follow the pointer from the right place.
    var avatarCenter: CGPoint {
        let f = shapeFrame
        switch model.mode {
        case .open: return CGPoint(x: f.minX + 106, y: f.maxY - model.metrics.menuBarHeight - 62)
        default: return CGPoint(x: f.minX + 28, y: f.maxY - model.metrics.menuBarHeight / 2)
        }
    }

    // MARK: Modes

    func set(_ mode: NotchMode) {
        guard mode != model.mode else { return }
        let reduce = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        let anim: Animation = reduce ? .easeInOut(duration: 0.15)
            : (mode == .idle ? .spring(response: 0.3, dampingFraction: 0.9) : .spring(response: 0.42, dampingFraction: 0.78))
        withAnimation(anim) { model.mode = mode }
        switch mode {
        case .open:
            panel?.makeKey()
            model.focusSearchToken += 1
        case .intent:
            panel?.makeKey()
            model.focusIntentToken += 1
        case .idle:
            model.selectedResult = 0
            if panel?.isKeyWindow == true { panel?.resignKey() }
        default:
            break
        }
        updateMouseThrough()
    }

    /// Opens the full notch (click, hotkey, workshadower://search). Toggles when already open.
    func toggleOpen() {
        if !isVisible { show() }
        if model.mode == .open { set(.idle) } else if model.mode != .intent { set(.open) }
    }

    func collapse() {
        if model.mode != .intent { set(.idle) }
    }

    // MARK: Toasts

    func toast(_ title: String, detail: String = "", tone: ToastTone = .good,
               action: String? = nil, duration: TimeInterval = 3.2, onAction: (() -> Void)? = nil) {
        // Never interrupt someone who is typing or answering the intent question.
        guard model.mode == .idle || model.mode == .peek || model.mode == .toast else { return }
        model.toastTitle = title
        model.toastDetail = detail
        model.toastTone = tone
        model.toastActionTitle = action
        model.toastAction = onAction
        set(.toast)
        toastTimer?.invalidate()
        let t = Timer(timeInterval: duration, repeats: false) { [weak self] _ in
            guard let self = self, self.model.mode == .toast else { return }
            if self.model.hovering { return }   // stays while you look at it; hover-off closes it
            self.set(.idle)
        }
        RunLoop.main.add(t, forMode: .common)
        toastTimer = t
    }

    // MARK: Intent

    /// Same contract as IntentPromptController.ask: completion gets the cleaned answer or nil.
    func askIntent(steps: Int, apps: [String], timeout: TimeInterval, completion: @escaping (String?) -> Void) {
        if !isVisible { show() }
        endIntent(nil)   // answer any earlier question first
        intentCompletion = completion
        model.intentText = ""
        model.intentSteps = steps
        model.intentAppHint = apps.first ?? ""
        model.intentSecondsLeft = Int(timeout)
        // The panel is non-activating: it takes the keyboard without pulling you out of your app.
        set(.intent)
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.model.intentSecondsLeft -= 1
            if self.model.intentSecondsLeft <= 0 { self.endIntent(self.model.intentText) }
        }
        RunLoop.main.add(t, forMode: .common)
        intentTimer = t
    }

    private func endIntent(_ text: String?) {
        intentTimer?.invalidate()
        intentTimer = nil
        guard let done = intentCompletion else { return }
        intentCompletion = nil
        let cleaned = RecordingPayload.cleanIntent(text)
        set(.idle)
        done(cleaned)
        toast(cleaned == nil ? "Saved without a note" : "Saved", detail: "Uploading…", tone: .good, duration: 1.8)
    }

    // MARK: Tracking (hover intent, click-through, outside clicks, keys)

    private func startTracking() {
        guard timer == nil else { return }
        let t = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in self?.track() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        globalClick = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            // A click anywhere else folds the notch away (but never drops the intent question).
            guard let self = self else { return }
            if self.model.mode == .open || self.model.mode == .peek || self.model.mode == .toast {
                if !self.hotFrame.contains(NSEvent.mouseLocation) { self.set(.idle) }
            }
        }
        localKeys = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard let self = self else { return e }
            return self.handleKey(e)
        }
    }

    private func stopTracking() {
        timer?.invalidate()
        timer = nil
        if let g = globalClick { NSEvent.removeMonitor(g) }
        if let l = localKeys { NSEvent.removeMonitor(l) }
        globalClick = nil
        localKeys = nil
    }

    private func track() {
        let m = model.metrics
        // Full-screen app on a display without a notch: stay out of the way unless something is happening.
        let suppress = !m.hasNotch && m.menuBarHidden && !hasStatus && model.mode == .idle
        if suppress != model.suppressed { model.suppressed = suppress }

        let inside = !suppress && hotFrame.contains(NSEvent.mouseLocation)
        if inside != model.hovering { model.hovering = inside }
        let now = Date()
        if inside {
            awaySince = nil
            if hoverSince == nil { hoverSince = now }
            if model.mode == .idle, let s = hoverSince, now.timeIntervalSince(s) >= Self.peekDelay { set(.peek) }
        } else {
            hoverSince = nil
            if awaySince == nil { awaySince = now }
            let away = now.timeIntervalSince(awaySince ?? now)
            switch model.mode {
            case .peek where away >= Self.peekGrace:
                set(.idle)
            case .toast where away >= Self.peekGrace && toastTimer?.isValid != true:
                set(.idle)
            case .open where away >= Self.openGrace:
                // Stay open while there is something typed; fold away if it's empty.
                if (search?.query ?? "").trimmingCharacters(in: .whitespaces).isEmpty { set(.idle) }
            default:
                break
            }
        }
        updateMouseThrough()
    }

    /// Clicks outside the black shape go to whatever is underneath.
    private func updateMouseThrough() {
        guard let p = panel else { return }
        let through = model.suppressed || !hotFrame.contains(NSEvent.mouseLocation)
        if p.ignoresMouseEvents != through { p.ignoresMouseEvents = through }
    }

    private func handleKey(_ e: NSEvent) -> NSEvent? {
        guard panel?.isKeyWindow == true else { return e }
        switch e.keyCode {
        case 53:   // esc
            if model.mode == .intent { endIntent(nil) } else { set(.idle) }
            return nil
        case 125:  // down
            guard model.mode == .open, let n = search?.results.count, n > 0 else { return e }
            model.selectedResult = min(model.selectedResult + 1, n - 1)
            return nil
        case 126:  // up
            guard model.mode == .open else { return e }
            model.selectedResult = max(model.selectedResult - 1, 0)
            return nil
        default:
            return e
        }
    }
}

/// Borderless, transparent, above the menu bar, on every Space, never in the window cycle.
final class NotchPanel: NSPanel {
    init(size: CGSize) {
        super.init(contentRect: NSRect(origin: .zero, size: size),
                   styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovable = false
        becomesKeyOnlyIfNeeded = true
        ignoresMouseEvents = true
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
    }
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
#endif
