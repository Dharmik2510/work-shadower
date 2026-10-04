#if os(macOS)
import AppKit
import SwiftUI
import DotCore

/// "What did you just do?" — asked once when a recording stops. The answer (one line) tells the
/// server what the task was, so it can title the skill and leave out steps that weren't part of it.
/// Never blocks: Skip, Esc, closing the panel or the timeout all continue without an answer.
final class IntentModel: ObservableObject {
    @Published var text = ""
    @Published var secondsLeft = 0
    var appsHint = ""
}

struct IntentView: View {
    @ObservedObject var m: IntentModel
    var onDone: (String?) -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What did you just do?")
                .font(.system(size: 15, weight: .semibold))
            Text("One line is plenty. It becomes the skill's title and helps leave out anything unrelated.")
                .font(.system(size: 11)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField(m.appsHint.isEmpty ? "e.g. Added a driver to an auto policy" : "e.g. Added a driver to an auto policy in \(m.appsHint)",
                      text: $m.text)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 13))
                .focused($focused)
                .onSubmit { onDone(m.text) }
            HStack {
                if m.secondsLeft > 0 && m.secondsLeft <= 30 {
                    Text("Saving without a note in \(m.secondsLeft)s").font(.system(size: 10)).foregroundColor(.secondary)
                }
                Spacer()
                Button("Skip") { onDone(nil) }.keyboardShortcut(.cancelAction)
                Button("Save") { onDone(m.text) }.keyboardShortcut(.defaultAction)
            }
            Text("Emails, card, SIN and phone numbers are blurred before anything leaves this Mac.")
                .font(.system(size: 10)).foregroundColor(.secondary)
        }
        .padding(16)
        .frame(width: 400)
        .onAppear { DispatchQueue.main.async { focused = true } }
    }
}

/// Owns the prompt panel. Main thread only. One question at a time: a new `ask` answers the
/// previous one with nil first, so a recording is never left waiting.
final class IntentPromptController: NSObject, NSWindowDelegate {
    private let model = IntentModel()
    private var panel: FloatingPanel?
    private var completion: ((String?) -> Void)?
    private var timer: Timer?

    func ask(near dotFrame: NSRect?, apps: [String], timeout: TimeInterval = 120,
             completion: @escaping (String?) -> Void) {
        finish(nil)  // answer any earlier question first
        model.text = ""
        model.appsHint = apps.first ?? ""
        model.secondsLeft = Int(timeout)
        self.completion = completion

        if panel == nil {
            let p = FloatingPanel(size: NSSize(width: 400, height: 170), title: "What did you just do?", nonActivating: false)
            p.contentView = NSHostingView(rootView: IntentView(m: model, onDone: { [weak self] in self?.finish($0) }))
            p.delegate = self
            panel = p
        }
        guard let p = panel else { return }
        if let dot = dotFrame, let screen = NSScreen.screens.first(where: { $0.frame.intersects(dot) }) ?? NSScreen.main {
            let vf = screen.visibleFrame
            let x = min(max(dot.maxX - p.frame.width, vf.minX + 8), vf.maxX - p.frame.width - 8)
            let y = min(max(dot.minY - p.frame.height - 6, vf.minY + 8), vf.maxY - p.frame.height - 8)
            p.setFrameOrigin(NSPoint(x: x, y: y))
        } else {
            p.center()
        }
        NSApp.activate(ignoringOtherApps: true)
        p.makeKeyAndOrderFront(nil)

        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.model.secondsLeft -= 1
            if self.model.secondsLeft <= 0 {
                let typed = self.model.text  // keep anything they typed
                self.finish(typed)
            }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    var isAsking: Bool { completion != nil }

    private func finish(_ answer: String?) {
        timer?.invalidate()
        timer = nil
        guard let done = completion else { return }
        completion = nil
        panel?.orderOut(nil)
        done(RecordingPayload.cleanIntent(answer))
    }

    func windowWillClose(_ notification: Notification) {
        finish(nil)
    }
}
#endif
