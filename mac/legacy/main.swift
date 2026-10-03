// FloatingDot — a tiny always-on-top companion for macOS.
// Sleeps with eyes closed, wakes (eyes open, blinks, watches the cursor) on hover.
// Click: toggle "recording" state (placeholder for the step recorder).
// Drag: move it. Right-click: Quit.

import AppKit
import SwiftUI

// MARK: - Model (drives all animation from one 60fps tick)

final class DotModel: ObservableObject {
    @Published var openness: CGFloat = 0      // 0 = closed, 1 = open
    @Published var pupil: CGSize = .zero
    @Published var t: Double = 0
    @Published var recording = false

    weak var window: NSWindow?
    private var timer: Timer?
    private var nextBlink = 3.0
    private var blinkStart = -10.0

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

        let awake = dist < 28 || recording
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

        // Pupils look toward the cursor (SwiftUI y is flipped)
        let reach = min(dist / 120, 1) * 2.2
        let angle = atan2(dy, dx)
        pupil = CGSize(width: cos(angle) * reach, height: -sin(angle) * reach)
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

    var body: some View {
        let bob = sin(m.t * 1.8) * 2.0
        let breathe = 1 + sin(m.t * 1.2) * 0.035 * Double(1 - m.openness)
        let glow = m.recording ? Color.red : Color(red: 0.45, green: 0.5, blue: 1)

        ZStack {
            if m.recording {
                Circle()
                    .stroke(Color.red, lineWidth: 2)
                    .frame(width: 43, height: 43)
                    .opacity(0.55 + 0.45 * sin(m.t * 4))
            }
            Circle()
                .fill(RadialGradient(
                    colors: [Color(red: 0.55, green: 0.62, blue: 1.0),
                             Color(red: 0.24, green: 0.2, blue: 0.72)],
                    center: UnitPoint(x: 0.35, y: 0.3),
                    startRadius: 1, endRadius: 26))
                .frame(width: 36, height: 36)
                .shadow(color: glow.opacity(0.55), radius: 8)

            HStack(spacing: 7) { eye; eye }
                .offset(y: -1)
        }
        .scaleEffect(breathe)
        .offset(y: bob)
        .frame(width: 64, height: 64)
        .contentShape(Circle())
        .onTapGesture { m.recording.toggle() }
        .gesture(drag)
        .contextMenu {
            Button(m.recording ? "Stop recording" : "Start recording") { m.recording.toggle() }
            Divider()
            Button("Quit") { NSApp.terminate(nil) }
        }
    }

    private var eye: some View {
        ZStack {
            Color.white
            Circle()
                .fill(Color(white: 0.1))
                .frame(width: 4, height: 4)
                .offset(m.pupil)
                .opacity(Double(m.openness))
        }
        .frame(width: 9, height: 10)
        .mask(Eye(openness: m.openness))
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
            .onEnded { _ in dragStartMouse = nil }
    }
}

// MARK: - Window

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

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    var panel: DotPanel!
    let model = DotModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let size: CGFloat = 64
        panel = DotPanel(size: size)

        let host = FirstMouseHostingView(rootView: DotView(m: model))
        host.frame = NSRect(x: 0, y: 0, width: size, height: size)
        panel.contentView = host

        if let vf = NSScreen.main?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: vf.maxX - size - 12, y: vf.maxY - size - 8))
        }
        panel.orderFrontRegardless()

        model.window = panel
        model.start()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // no Dock icon
app.run()
