#if os(macOS)
import AppKit
import SwiftUI
import DotCore

/// Thread-safe pause/stop flags shared by the HUD (main thread) and the
/// replay task (background).
final class ReplayControl {
    private let lock = NSLock()
    private var _paused = false
    private var _stopped = false

    var paused: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _paused }
        set { lock.lock(); _paused = newValue; lock.unlock() }
    }

    var stopped: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _stopped }
        set { lock.lock(); _stopped = newValue; lock.unlock() }
    }

    func reset() {
        lock.lock()
        _paused = false
        _stopped = false
        lock.unlock()
    }
}

enum HUDChoice {
    case proceed   // "Run this step" / "Confirm"
    case done      // "I did it" (human performed the step)
    case skip
    case stop
}

enum HUDPhase: Equatable {
    case working(String)
    case confirm(irreversible: Bool)
    case human(String)
    case finished(String)
}

final class ReplayHUDModel: ObservableObject {
    @Published var skillTitle = ""
    @Published var stepNumber = 0
    @Published var stepCount = 0
    @Published var stepTitle = ""
    @Published var instruction = ""
    @Published var phase: HUDPhase = .working("Starting…")
    @Published var paused = false
    @Published var modeText = ""

    let control = ReplayControl()
    private var pending: CheckedContinuation<HUDChoice, Never>?

    /// Main thread only.
    func answer(_ choice: HUDChoice) {
        let p = pending
        pending = nil
        p?.resume(returning: choice)
    }

    /// Callable from any thread; suspends until a HUD button is pressed.
    func ask(_ phase: HUDPhase) async -> HUDChoice {
        if control.stopped { return .stop }
        return await withCheckedContinuation { (cont: CheckedContinuation<HUDChoice, Never>) in
            DispatchQueue.main.async {
                if self.control.stopped {
                    cont.resume(returning: .stop)
                    return
                }
                self.pending?.resume(returning: .stop)
                self.pending = cont
                self.phase = phase
            }
        }
    }

    func togglePause() {
        paused.toggle()
        control.paused = paused
    }

    func stop() {
        control.stopped = true
        paused = false
        control.paused = false
        answer(.stop)
    }
}

struct ReplayHUDView: View {
    @ObservedObject var m: ReplayHUDModel
    var onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle().fill(DotView.green).frame(width: 8, height: 8)
                Text(m.skillTitle).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                Spacer()
                if m.stepCount > 0 {
                    Text("Step \(m.stepNumber)/\(m.stepCount)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                }
            }
            Text(m.stepTitle).font(.system(size: 13, weight: .medium)).lineLimit(2)
            Text(m.instruction).font(.system(size: 12)).foregroundColor(.secondary)
                .lineLimit(4).fixedSize(horizontal: false, vertical: true)
            phaseView
            if !m.modeText.isEmpty {
                Text(m.modeText).font(.system(size: 10)).foregroundColor(.secondary)
            }
        }
        .padding(12)
        .frame(width: 320)
    }

    @ViewBuilder
    private var phaseView: some View {
        switch m.phase {
        case .working(let text):
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(m.paused ? "Paused" : text).font(.system(size: 11)).foregroundColor(.secondary).lineLimit(2)
                Spacer()
                Button(m.paused ? "Resume" : "Pause") { m.togglePause() }
                Button("Stop") { m.stop() }
            }
        case .confirm(let irreversible):
            VStack(alignment: .leading, spacing: 6) {
                if irreversible {
                    Label("This step can't be undone. Check the screen, then confirm.", systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 11)).foregroundColor(DotView.amber)
                }
                HStack {
                    Button(irreversible ? "Confirm & run" : "Run step") { m.answer(.proceed) }
                        .keyboardShortcut(.defaultAction)
                    Button("I'll do it") { m.answer(.done) }
                    Button("Skip") { m.answer(.skip) }
                    Spacer()
                    Button("Stop") { m.stop() }
                }
            }
        case .human(let reason):
            VStack(alignment: .leading, spacing: 6) {
                Text(reason).font(.system(size: 11)).foregroundColor(DotView.amber)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("Done") { m.answer(.done) }.keyboardShortcut(.defaultAction)
                    Button("Skip") { m.answer(.skip) }
                    Spacer()
                    Button("Stop") { m.stop() }
                }
            }
        case .finished(let text):
            HStack {
                Text(text).font(.system(size: 11)).lineLimit(2)
                Spacer()
                Button("Close") { onClose() }
            }
        }
    }
}

/// Owns the HUD panel; main thread only.
final class ReplayHUDController {
    let model = ReplayHUDModel()
    private var panel: FloatingPanel?

    func show(near dotFrame: NSRect?) {
        if panel == nil {
            let p = FloatingPanel(size: NSSize(width: 320, height: 170), title: "Dot", nonActivating: true)
            let host = FirstMouseHostingView(rootView: ReplayHUDView(m: model, onClose: { [weak self] in self?.hide() }))
            p.contentView = host
            p.level = .statusBar
            panel = p
        }
        guard let p = panel else { return }
        if let host = p.contentView {
            let fit = host.fittingSize
            p.setContentSize(NSSize(width: 320, height: max(120, fit.height)))
        }
        if let dot = dotFrame, let screen = NSScreen.screens.first(where: { $0.frame.intersects(dot) }) ?? NSScreen.main {
            let vf = screen.visibleFrame
            var x = dot.maxX - p.frame.width
            var y = dot.minY - p.frame.height - 6
            if y < vf.minY { y = dot.maxY + 6 }
            x = min(max(x, vf.minX + 8), vf.maxX - p.frame.width - 8)
            y = min(max(y, vf.minY + 8), vf.maxY - p.frame.height - 8)
            p.setFrameOrigin(NSPoint(x: x, y: y))
        }
        p.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    /// HUD frame in CG global coordinates (top-left origin), for click avoidance.
    var cgFrame: CGRect? {
        guard let p = panel, p.isVisible, let primary = NSScreen.screens.first else { return nil }
        let f = p.frame
        return CGRect(x: f.minX, y: primary.frame.maxY - f.maxY, width: f.width, height: f.height)
    }
}

// MARK: - Inputs form

final class InputsFormModel: ObservableObject {
    struct Field: Identifiable {
        let id: String
        let description: String
        let example: String
        var value: String
    }

    @Published var title = ""
    @Published var fields: [Field] = []
    @Published var mode: RunMode = .guided
    var onFinish: ((Bool) -> Void)?

    var values: [String: String] {
        var out: [String: String] = [:]
        for f in fields { out[f.id] = f.value }
        return out
    }

    var canStart: Bool {
        return fields.allSatisfy { !$0.value.trimmingCharacters(in: .whitespaces).isEmpty }
    }
}

struct InputsFormView: View {
    @ObservedObject var m: InputsFormModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Run “\(m.title)”").font(.headline).lineLimit(2)
            if !m.fields.isEmpty {
                ForEach($m.fields) { $field in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(field.id).font(.system(size: 11, weight: .semibold))
                        TextField(field.example.isEmpty ? field.id : "e.g. \(field.example)", text: $field.value)
                            .textFieldStyle(.roundedBorder)
                        if !field.description.isEmpty {
                            Text(field.description).font(.system(size: 10)).foregroundColor(.secondary)
                        }
                    }
                }
            }
            Picker("Mode", selection: $m.mode) {
                Text("Guided — confirm each step").tag(RunMode.guided)
                Text("Auto — run until something needs me").tag(RunMode.auto)
            }
            .pickerStyle(.radioGroup)
            Text("Steps that can't be undone always wait for your confirmation.")
                .font(.system(size: 10)).foregroundColor(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { m.onFinish?(false) }.keyboardShortcut(.cancelAction)
                Button("Start") { m.onFinish?(true) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!m.canStart)
            }
        }
        .padding(16)
        .frame(width: 380)
    }
}

/// Shows the inputs form and returns values + mode, or nil if cancelled. Main thread only.
final class InputsFormController {
    private var panel: FloatingPanel?

    func present(title: String, inputs: [SkillInput], defaultMode: RunMode,
                 completion: @escaping (([String: String], RunMode)?) -> Void) {
        let model = InputsFormModel()
        model.title = title
        model.mode = defaultMode
        model.fields = inputs.map { InputsFormModel.Field(id: $0.name, description: $0.description ?? "",
                                                           example: $0.example ?? "", value: "") }
        let p = FloatingPanel(size: NSSize(width: 380, height: 200), title: "Run skill", nonActivating: false)
        model.onFinish = { [weak self, weak model] ok in
            guard let model = model else { return }
            let result = ok ? (model.values, model.mode) : nil
            self?.panel?.orderOut(nil)
            self?.panel = nil
            completion(result)
        }
        let host = NSHostingView(rootView: InputsFormView(m: model))
        p.contentView = host
        p.setContentSize(host.fittingSize)
        p.center()
        panel = p
        NSApp.activate(ignoringOtherApps: true)
        p.makeKeyAndOrderFront(nil)
    }
}
#endif
