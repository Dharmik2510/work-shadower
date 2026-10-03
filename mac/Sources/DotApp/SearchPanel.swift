#if os(macOS)
import AppKit
import SwiftUI
import DotCore

/// "How do I…?" search near the dot. Opened from the menu or the global hotkey.
final class SearchModel: ObservableObject {
    @Published var query = "" {
        didSet { scheduleSearch() }
    }
    @Published var results: [SkillSummary] = []
    @Published var loading = false
    @Published var message: String?

    var api: APIClient?
    var onLearn: (SkillSummary) -> Void = { _ in }
    var onRun: (SkillSummary) -> Void = { _ in }
    var replayEnabled = true

    private var pending: DispatchWorkItem?
    private var generation = 0

    private func scheduleSearch() {
        pending?.cancel()
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard q.count >= 2 else {
            results = []
            message = nil
            loading = false
            return
        }
        let item = DispatchWorkItem { [weak self] in self?.search(q) }
        pending = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: item)
    }

    private func search(_ q: String) {
        guard let api = api else { return }
        generation += 1
        let gen = generation
        loading = true
        Task {
            do {
                let items = try await api.search(q, limit: 8)
                DispatchQueue.main.async {
                    guard gen == self.generation else { return }
                    self.loading = false
                    self.results = items
                    self.message = items.isEmpty ? "No skills match yet. Record one with the dot!" : nil
                }
            } catch {
                DispatchQueue.main.async {
                    guard gen == self.generation else { return }
                    self.loading = false
                    self.results = []
                    self.message = (error as? APIError)?.isAuthFailure == true
                        ? "Sign in from Settings to search." : "Search is unavailable right now."
                }
            }
        }
    }
}

struct SearchView: View {
    @ObservedObject var m: SearchModel
    var onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                TextField("How do I…?", text: $m.query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 16))
                    .onSubmit {
                        if let first = m.results.first { m.onLearn(first) }
                    }
                if m.loading { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 4)
            Divider()
            if let msg = m.message {
                Text(msg).font(.system(size: 12)).foregroundColor(.secondary).padding(.vertical, 6)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(m.results) { s in row(s) }
                }
            }
            .frame(maxHeight: 320)
            HStack {
                Text("Enter opens the top result").font(.system(size: 10)).foregroundColor(.secondary)
                Spacer()
                Button("Close") { onClose() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(14)
        .frame(width: 460)
    }

    private func row(_ s: SkillSummary) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(s.title).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                if !s.goal.isEmpty {
                    Text(s.goal).font(.system(size: 11)).foregroundColor(.secondary).lineLimit(2)
                }
                let meta = ([s.team?.name, s.owner?.name].compactMap { $0 } + s.apps.prefix(2)).joined(separator: " · ")
                if !meta.isEmpty {
                    Text(meta).font(.system(size: 10)).foregroundColor(.secondary).lineLimit(1)
                }
            }
            Spacer()
            Button("Learn") { m.onLearn(s) }
            Button("Do it") { m.onRun(s) }
                .disabled(!m.replayEnabled)
                .help(m.replayEnabled ? "Run this skill on your Mac" : "Replay is turned off by your admin")
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
    }
}

/// Owns the search panel. Main thread only.
final class SearchPanelController {
    let model = SearchModel()
    private var panel: FloatingPanel?

    func toggle(near dotFrame: NSRect?) {
        if let p = panel, p.isVisible {
            hide()
            return
        }
        show(near: dotFrame)
    }

    func show(near dotFrame: NSRect?) {
        if panel == nil {
            let p = FloatingPanel(size: NSSize(width: 460, height: 120), title: "Search skills", nonActivating: false)
            p.contentView = NSHostingView(rootView: SearchView(m: model, onClose: { [weak self] in self?.hide() }))
            panel = p
        }
        guard let p = panel else { return }
        p.setContentSize(NSSize(width: 460, height: 440))
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
    }

    func hide() {
        panel?.orderOut(nil)
    }
}
#endif
