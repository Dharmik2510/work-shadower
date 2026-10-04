#if os(macOS)
// The floating dot — ported from legacy/main.swift with identical look and
// behavior (sleepy crescent eyes, wake on hover, blink, pupils follow cursor,
// click, drag, right-click menu) plus status rings for the new states.
import AppKit
import SwiftUI
import DotCore

enum DotActivity: Equatable {
    case idle
    case recording
    case replaying
}

// MARK: - Model (drives all animation from one 60fps tick)

final class DotModel: ObservableObject {
    @Published var openness: CGFloat = 0      // 0 = closed, 1 = open
    @Published var smile: CGFloat = 0         // 0 = calm, 1 = big smile (on hover)
    @Published var avatar: AvatarKind = .orb  // the character the user picked
    @Published var pupil: CGSize = .zero
    @Published var t: Double = 0
    @Published var hovering = false

    // App state (set by AppController on the main thread)
    @Published var activity: DotActivity = .idle
    @Published var recordingStartedAt: Date?
    @Published var uploading = false
    @Published var uploadProgress: Double?     // nil = indeterminate
    @Published var errorMessage: String?
    @Published var pendingUploads = 0
    @Published var replayEnabled = true
    @Published var recordingEnabled = true

    // Actions (wired by AppController)
    var onClick: () -> Void = {}
    var onSearch: () -> Void = {}
    var onOpenLibrary: () -> Void = {}
    var onRetryUploads: () -> Void = {}
    var onSettings: () -> Void = {}
    var onChooseAvatar: (AvatarKind) -> Void = { _ in }
    var onHoverStart: () -> Void = {}
    var onMoved: (CGPoint) -> Void = { _ in }

    weak var window: NSWindow?
    private var timer: Timer?
    private var nextBlink = 3.0
    private var blinkStart = -10.0

    var recording: Bool { return activity == .recording }
    var replaying: Bool { return activity == .replaying }

    func start() {
        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func tick() {
        t += 1.0 / 60.0
        guard let w = window else { return }

        let center = CGPoint(x: w.frame.midX, y: w.frame.midY)
        let mouse = NSEvent.mouseLocation
        let dx = mouse.x - center.x, dy = mouse.y - center.y
        let dist = hypot(dx, dy)

        let near = dist < 28
        if near != hovering {
            hovering = near
            if near { onHoverStart() }
        }
        let awake = near || recording || replaying
        var target: CGFloat = awake ? 1 : 0
        var speed: CGFloat = 0.18

        // Random blinks while awake
        if awake {
            if t >= nextBlink {
                blinkStart = t
                nextBlink = t + Double.random(in: 2.5...5.5)
            }
            if t - blinkStart < 0.14 { target = 0.05; speed = 0.45 }
        }
        openness += (target - openness) * speed
        // Smile while the pointer is on the dot (and not while it's busy recording).
        let smileTarget: CGFloat = (near && !recording) ? 1 : 0
        smile += (smileTarget - smile) * 0.2

        // Pupils look toward the cursor (SwiftUI y is flipped)
        let reach = min(dist / 120, 1) * 2.2
        let angle = atan2(dy, dx)
        pupil = CGSize(width: cos(angle) * reach, height: -sin(angle) * reach)
    }

    var elapsedText: String {
        guard let start = recordingStartedAt else { return "" }
        let s = max(0, Int(Date().timeIntervalSince(start)))
        return String(format: "%02d:%02d", s / 60, s % 60)
    }

    var tooltip: String {
        switch activity {
        case .recording: return "Recording \(elapsedText) — click to stop"
        case .replaying: return "Running a skill"
        case .idle:
            if let e = errorMessage { return e }
            if uploading { return "Uploading…" }
            if pendingUploads > 0 { return "\(pendingUploads) recording(s) waiting to upload" }
            return recordingEnabled ? "Click to record a workflow" : "Recording is turned off by your admin"
        }
    }
}

// MARK: - Eye shape: sleepy crescent (closed) → lens (open)

struct Eye: Shape {
    var openness: CGFloat
    var animatableData: CGFloat {
        get { openness }
        set { openness = newValue }
    }

    func path(in r: CGRect) -> Path {
        let h = r.height
        let topCtrl = r.midY + h * 0.4 + (-h - h * 0.4) * openness
        let bottomCtrl = r.midY + h * 0.8 + (h - h * 0.8) * openness
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.midY))
        p.addQuadCurve(to: CGPoint(x: r.maxX, y: r.midY), control: CGPoint(x: r.midX, y: topCtrl))
        p.addQuadCurve(to: CGPoint(x: r.minX, y: r.midY), control: CGPoint(x: r.midX, y: bottomCtrl))
        p.closeSubpath()
        return p
    }
}

// MARK: - View

struct DotView: View {
    @ObservedObject var m: DotModel
    @State private var dragStartMouse: CGPoint?
    @State private var dragStartOrigin: CGPoint = .zero

    static let amber = Color(red: 1.0, green: 0.72, blue: 0.2)
    static let green = Color(red: 0.25, green: 0.85, blue: 0.45)

    private var glow: Color {
        if m.recording { return .red }
        if m.replaying { return DotView.green }
        if m.errorMessage != nil { return DotView.amber }
        return Color(red: 0.45, green: 0.5, blue: 1)
    }

    var body: some View {
        let bob = sin(m.t * 1.8) * 2.0
        let breathe = 1 + sin(m.t * 1.2) * 0.035 * Double(1 - m.openness)
        // a small happy hop as the smile arrives
        let hop = -sin(Double(m.smile) * .pi) * 3

        ZStack {
            ring
            DotCharacter(kind: m.avatar, openness: m.openness, smile: m.smile, pupil: m.pupil, glow: glow, size: 44)
                .offset(y: CGFloat(hop))
        }
        .scaleEffect(breathe)
        .offset(y: bob)
        .frame(width: 64, height: 64)
        .overlay(alignment: .bottom) { timerBadge }
        .contentShape(Circle())
        .onTapGesture { m.onClick() }
        .gesture(drag)
        .help(m.tooltip)
        .contextMenu { menu }
    }

    @ViewBuilder
    private var ring: some View {
        if m.recording {
            Circle()
                .stroke(Color.red, lineWidth: 2)
                .frame(width: 52, height: 52)
                .opacity(0.55 + 0.45 * sin(m.t * 4))
        } else if m.replaying {
            Circle()
                .stroke(DotView.green, lineWidth: 2)
                .frame(width: 52, height: 52)
                .opacity(0.6 + 0.3 * sin(m.t * 2))
        } else if m.errorMessage != nil {
            Circle()
                .stroke(DotView.amber, lineWidth: 2)
                .frame(width: 52, height: 52)
                .opacity(0.85)
        } else if m.uploading {
            // Subtle progress arc; spins when progress is unknown.
            let p = m.uploadProgress
            Circle()
                .trim(from: 0, to: CGFloat(max(0.08, min(1, p ?? 0.25))))
                .stroke(Color(red: 0.55, green: 0.62, blue: 1.0), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: 52, height: 52)
                .rotationEffect(.degrees(p == nil ? (m.t * 240).truncatingRemainder(dividingBy: 360) - 90 : -90))
                .opacity(0.6)
        }
    }

    @ViewBuilder
    private var timerBadge: some View {
        if m.recording && m.hovering {
            Text(m.elapsedText)
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .foregroundColor(.white)
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(Capsule().fill(Color.red.opacity(0.85)))
                .offset(y: 2)
                .allowsHitTesting(false)
        }
    }

    @ViewBuilder
    private var menu: some View {
        if m.recording {
            Button("Stop recording") { m.onClick() }
        } else {
            Button("Start recording") { m.onClick() }
                .disabled(m.replaying || !m.recordingEnabled)
        }
        Button("Search skills…") { m.onSearch() }
        Button("Open library") { m.onOpenLibrary() }
        Button(m.pendingUploads > 0 ? "Pending uploads (\(m.pendingUploads)) — retry now" : "Pending uploads (0)") {
            m.onRetryUploads()
        }
        .disabled(m.pendingUploads == 0)
        Divider()
        Menu("Your dot") {
            ForEach(AvatarKind.allCases, id: \.self) { k in
                Button(action: { m.onChooseAvatar(k) }) {
                    if m.avatar == k {
                        Label(k.displayName, systemImage: "checkmark")
                    } else {
                        Text(k.displayName)
                    }
                }
            }
        }
        Button("Settings…") { m.onSettings() }
        Button("Quit") { NSApp.terminate(nil) }
    }

    private var drag: some Gesture {
        DragGesture(minimumDistance: 3)
            .onChanged { _ in
                guard let w = m.window else { return }
                let mouse = NSEvent.mouseLocation
                if dragStartMouse == nil {
                    dragStartMouse = mouse
                    dragStartOrigin = w.frame.origin
                }
                guard let start = dragStartMouse else { return }
                w.setFrameOrigin(CGPoint(x: dragStartOrigin.x + mouse.x - start.x,
                                         y: dragStartOrigin.y + mouse.y - start.y))
            }
            .onEnded { _ in
                dragStartMouse = nil
                if let w = m.window { m.onMoved(w.frame.origin) }
            }
    }
}

// MARK: - Windows

final class DotPanel: NSPanel {
    init(size: CGFloat) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: size, height: size),
                   styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
    }
    override var canBecomeKey: Bool { true }
}

final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// A small floating utility panel (search, HUD, inputs form) that can take
/// keyboard focus without a Dock icon.
final class FloatingPanel: NSPanel {
    init(size: NSSize, title: String, nonActivating: Bool) {
        var style: NSWindow.StyleMask = [.titled, .closable, .fullSizeContentView]
        if nonActivating { style.insert(.nonactivatingPanel) }
        super.init(contentRect: NSRect(origin: .zero, size: size), styleMask: style, backing: .buffered, defer: false)
        self.title = title
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        isMovableByWindowBackground = true
        isFloatingPanel = true
        level = .floating
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        standardWindowButton(.miniaturizeButton)?.isHidden = true
        standardWindowButton(.zoomButton)?.isHidden = true
    }
    override var canBecomeKey: Bool { true }
}
#endif
