#if os(macOS)
// What the notch shows in each state. Visual rules:
//  - The shape is pure black so, at rest, it is indistinguishable from the hardware notch.
//  - Nothing is drawn behind the camera housing: the top row only uses the "ears" either side.
//  - One accent per state: red = recording, green = running a skill, amber = needs you, blue = uploading.
import AppKit
import SwiftUI
import DotCore

private enum Ink {
    static let primary = Color.white
    static let secondary = Color.white.opacity(0.56)
    static let faint = Color.white.opacity(0.1)
    static let line = Color.white.opacity(0.09)
    static let red = Color(red: 1.0, green: 0.27, blue: 0.23)
    static let green = Color(red: 0.19, green: 0.82, blue: 0.35)
    static let amber = Color(red: 1.0, green: 0.84, blue: 0.04)
    static let blue = Color(red: 0.04, green: 0.52, blue: 1.0)
}

struct NotchRootView: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel
    @ObservedObject var search: SearchModel
    @ObservedObject var hud: ReplayHUDModel

    private var hasStatus: Bool { dot.activity != .idle || dot.uploading || dot.errorMessage != nil }
    private var size: CGSize { NotchLayout.size(model.mode, metrics: model.metrics, hasStatus: hasStatus) }
    private var radius: CGFloat { NotchLayout.bottomRadius(model.mode, metrics: model.metrics) }
    private var expanded: Bool { model.mode != .idle }

    var body: some View {
        let shape = NotchShape(width: size.width, height: size.height, radius: radius,
                               flare: expanded || hasStatus ? 8 : (model.metrics.hasNotch ? 0 : 3))
        ZStack(alignment: .top) {
            // Clicking the black itself (not a control) opens the full notch.
            shape
                .fill(Color.black)
                .shadow(color: .black.opacity(expanded ? 0.38 : 0), radius: expanded ? 18 : 0, y: expanded ? 8 : 0)
                .contentShape(shape)
                .onTapGesture {
                    if model.mode == .idle || model.mode == .peek || model.mode == .toast { model.requestMode(.open) }
                }
            content
                .frame(width: size.width, height: size.height, alignment: .top)
                .clipShape(shape)
        }
        .frame(width: NotchLayout.canvas.width, height: NotchLayout.canvas.height, alignment: .top)
        .opacity(model.suppressed ? 0 : 1)
        .contextMenu { NotchMenu(model: model, dot: dot) }
        .environment(\.colorScheme, .dark)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Work Shadower")
    }

    @ViewBuilder
    private var content: some View {
        switch model.mode {
        case .idle:
            if hasStatus { StatusEars(model: model, dot: dot, hud: hud).transition(.opacity) }
        case .peek:
            PeekView(model: model, dot: dot, hud: hud).transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
        case .open:
            OpenView(model: model, dot: dot, search: search).transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
        case .intent:
            IntentNotchView(model: model, dot: dot).transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
        case .toast:
            ToastNotchView(model: model, dot: dot).transition(.opacity)
        }
    }
}

// MARK: - Pieces

/// The user's character with live eyes; smiles while the pointer is on the notch.
private struct LiveAvatar: View {
    @ObservedObject var dot: DotModel
    var size: CGFloat
    var happy = false

    var body: some View {
        DotCharacter(kind: dot.avatar, openness: happy ? 1 : dot.openness, smile: happy ? 1 : dot.smile,
                     pupil: dot.pupil, glow: .clear, size: size)
            .accessibilityHidden(true)
    }
}

/// The two sides of the top row, either side of the camera housing.
private struct TopRow<Leading: View, Trailing: View>: View {
    let metrics: NotchMetrics
    @ViewBuilder var leading: Leading
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 7) { leading }
            Spacer(minLength: metrics.hasNotch ? metrics.notchSize.width + 20 : 24)
            HStack(spacing: 7) { trailing }
        }
        .frame(height: metrics.menuBarHeight)
        .padding(.horizontal, 16)
    }
}

private struct RecDot: View {
    var color = Ink.red
    var pulse: Double
    var body: some View {
        Circle().fill(color).frame(width: 8, height: 8)
            .overlay(Circle().stroke(color.opacity(0.35), lineWidth: 4).scaleEffect(1 + 0.25 * pulse))
    }
}

private struct ProgressRing: View {
    var value: Double?   // nil = indeterminate
    var color: Color
    var t: Double
    var body: some View {
        ZStack {
            Circle().stroke(Ink.faint, lineWidth: 2.5)
            Circle()
                .trim(from: 0, to: CGFloat(max(0.08, min(1, value ?? 0.28))))
                .stroke(color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .rotationEffect(.degrees(value == nil ? (t * 300).truncatingRemainder(dividingBy: 360) : -90))
        }
        .frame(width: 15, height: 15)
    }
}

/// Pill button used throughout the notch.
private struct NotchButtonStyle: ButtonStyle {
    enum Kind { case primary(Color), plain, ghost, light }
    var kind: Kind = .plain

    func makeBody(configuration: Configuration) -> some View {
        Wrapper(configuration: configuration, kind: kind)
    }

    private struct Wrapper: View {
        let configuration: Configuration
        let kind: Kind
        @State private var hover = false
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            let (bg, fg): (Color, Color) = {
                switch kind {
                case .primary(let c): return (c, .white)
                case .plain: return (Color.white.opacity(hover ? 0.16 : 0.1), Ink.primary)
                case .ghost: return (Color.white.opacity(hover ? 0.08 : 0), Ink.secondary)
                case .light: return (Color.white.opacity(hover ? 1 : 0.92), .black)
                }
            }()
            configuration.label
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(fg)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(Capsule().fill(bg))
                .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.4)
                .scaleEffect(configuration.isPressed ? 0.97 : 1)
                .onHover { hover = $0 }
                .animation(.easeOut(duration: 0.12), value: hover)
        }
    }
}

// MARK: - Idle with a live activity

private struct StatusEars: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel
    @ObservedObject var hud: ReplayHUDModel

    var body: some View {
        TopRow(metrics: model.metrics) {
            leading
        } trailing: {
            trailing
        }
        .padding(.horizontal, -4)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(dot.tooltip)
    }

    @ViewBuilder private var leading: some View {
        if dot.recording {
            RecDot(pulse: (sin(dot.t * 4) + 1) / 2)
            Text(dot.elapsedText).font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundColor(Ink.primary)
        } else if dot.replaying {
            RecDot(color: Ink.green, pulse: (sin(dot.t * 2) + 1) / 2)
            Text(hud.stepCount > 0 ? "\(hud.stepNumber)/\(hud.stepCount)" : "…")
                .font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundColor(Ink.green)
        } else if dot.errorMessage != nil {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 11)).foregroundColor(Ink.amber)
        } else {
            LiveAvatar(dot: dot, size: 20)
        }
    }

    @ViewBuilder private var trailing: some View {
        if dot.replaying {
            ProgressRing(value: hud.stepCount > 0 ? Double(hud.stepNumber) / Double(hud.stepCount) : nil, color: Ink.green, t: dot.t)
        } else if dot.uploading && !dot.recording {
            ProgressRing(value: dot.uploadProgress, color: Ink.blue, t: dot.t)
        } else {
            LiveAvatar(dot: dot, size: 20)
        }
    }
}

// MARK: - Peek

private struct PeekView: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel
    @ObservedObject var hud: ReplayHUDModel

    var body: some View {
        VStack(spacing: 0) {
            TopRow(metrics: model.metrics) {
                LiveAvatar(dot: dot, size: 22)
                if dot.recording {
                    Text(dot.elapsedText).font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundColor(Ink.red)
                }
            } trailing: {
                if dot.uploading {
                    ProgressRing(value: dot.uploadProgress, color: Ink.blue, t: dot.t)
                } else if dot.pendingUploads > 0 {
                    Text("\(dot.pendingUploads) waiting").font(.system(size: 11)).foregroundColor(Ink.secondary)
                }
            }
            HStack(spacing: 8) { actions }
                .padding(.horizontal, 16)
                .frame(maxHeight: .infinity)
        }
    }

    @ViewBuilder private var actions: some View {
        if dot.recording {
            Text("Recording. Do the task as usual.").font(.system(size: 12)).foregroundColor(Ink.secondary).lineLimit(1)
            Spacer(minLength: 8)
            Button("Discard") { model.onDiscardRecording() }.buttonStyle(NotchButtonStyle(kind: .ghost))
            Button { model.onRecordToggle() } label: { Label("Stop", systemImage: "stop.fill") }
                .buttonStyle(NotchButtonStyle(kind: .primary(Ink.red)))
                .keyboardShortcut(.defaultAction)
        } else if dot.replaying {
            VStack(alignment: .leading, spacing: 1) {
                Text(hud.stepTitle.isEmpty ? hud.skillTitle : hud.stepTitle)
                    .font(.system(size: 12, weight: .semibold)).foregroundColor(Ink.primary).lineLimit(1)
                Text(replayStatus).font(.system(size: 11)).foregroundColor(needsYou ? Ink.amber : Ink.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            if needsYou {
                Button("Show") { model.onShowReplayHUD() }.buttonStyle(NotchButtonStyle(kind: .light))
            } else {
                Button { hud.togglePause() } label: { Image(systemName: hud.paused ? "play.fill" : "pause.fill") }
                    .buttonStyle(NotchButtonStyle(kind: .plain))
                    .accessibilityLabel(hud.paused ? "Resume" : "Pause")
            }
            Button { hud.stop() } label: { Image(systemName: "stop.fill") }
                .buttonStyle(NotchButtonStyle(kind: .plain))
                .accessibilityLabel("Stop running")
        } else if let err = dot.errorMessage {
            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(Ink.amber)
            Text(err).font(.system(size: 12)).foregroundColor(Ink.primary).lineLimit(2)
            Spacer(minLength: 8)
            Button("Settings") { model.onSettings() }.buttonStyle(NotchButtonStyle(kind: .plain))
        } else {
            Button { model.onRecordToggle() } label: { Label("Record a task", systemImage: "record.circle") }
                .buttonStyle(NotchButtonStyle(kind: .primary(Ink.red)))
                .disabled(!dot.recordingEnabled)
                .help(dot.recordingEnabled ? "Start recording a workflow" : "Recording is turned off by your admin")
            Button { model.requestMode(.open) } label: { Label("How do I…?", systemImage: "magnifyingglass") }
                .buttonStyle(NotchButtonStyle(kind: .plain))
            Button { model.onLibrary() } label: { Label("Library", systemImage: "books.vertical") }
                .buttonStyle(NotchButtonStyle(kind: .plain))
            Spacer(minLength: 4)
            Text("⌥⌘Space").font(.system(size: 10, design: .monospaced)).foregroundColor(Ink.secondary)
        }
    }

    private var needsYou: Bool {
        switch hud.phase {
        case .confirm, .human: return true
        default: return false
        }
    }

    private var replayStatus: String {
        let step = hud.stepCount > 0 ? "Step \(hud.stepNumber) of \(hud.stepCount)" : "Starting"
        switch hud.phase {
        case .confirm(let irreversible): return irreversible ? "\(step) · can't be undone, waiting for you" : "\(step) · waiting for you"
        case .human: return "\(step) · needs you to do this one"
        case .finished(let text): return text
        case .working: return hud.paused ? "\(step) · paused" : step
        }
    }
}

// MARK: - Open

private struct OpenView: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel
    @ObservedObject var search: SearchModel

    var body: some View {
        VStack(spacing: 0) {
            TopRow(metrics: model.metrics) {
                LiveAvatar(dot: dot, size: 20)
                Text(model.firstName.isEmpty ? "Work Shadower" : "Hi \(model.firstName)")
                    .font(.system(size: 13, weight: .semibold)).foregroundColor(Ink.primary)
            } trailing: {
                Button { model.onLibrary() } label: { Text("Library") }.buttonStyle(NotchButtonStyle(kind: .ghost))
                Button { model.onSettings() } label: { Image(systemName: "gearshape") }
                    .buttonStyle(NotchButtonStyle(kind: .ghost))
                    .accessibilityLabel("Settings")
            }
            HStack(alignment: .top, spacing: 0) {
                left.frame(width: 176)
                Rectangle().fill(Ink.line).frame(width: 1).padding(.vertical, 4)
                SearchColumn(model: model, search: search).padding(.leading, 16)
            }
            .padding(.horizontal, 18)
            .padding(.top, 8)
            .padding(.bottom, 16)
        }
    }

    private var left: some View {
        VStack(spacing: 10) {
            LiveAvatar(dot: dot, size: 74)
                .padding(.top, 2)
            if dot.recording {
                Button { model.onRecordToggle() } label: {
                    Label("Stop · \(dot.elapsedText)", systemImage: "stop.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(NotchButtonStyle(kind: .primary(Ink.red)))
            } else {
                Button { model.onRecordToggle() } label: {
                    Label("Record a task", systemImage: "record.circle").frame(maxWidth: .infinity)
                }
                .buttonStyle(NotchButtonStyle(kind: .primary(Ink.red)))
                .disabled(!dot.recordingEnabled || dot.replaying)
            }
            Text(statusLine).font(.system(size: 11)).foregroundColor(statusColor)
                .multilineTextAlignment(.center).lineLimit(2).frame(maxWidth: .infinity)
        }
        .padding(.trailing, 16)
    }

    private var statusLine: String {
        if let e = dot.errorMessage { return e }
        if !dot.recordingEnabled { return "Recording is turned off by your admin." }
        if dot.recording { return "Do the task as you normally would." }
        if dot.uploading { return "Uploading your recording…" }
        if dot.pendingUploads > 0 { return "\(dot.pendingUploads) recording\(dot.pendingUploads == 1 ? "" : "s") waiting to upload" }
        return "Do a task once. Colleagues can learn it or have it done for them."
    }

    private var statusColor: Color { dot.errorMessage != nil ? Ink.amber : Ink.secondary }
}

private struct SearchColumn: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var search: SearchModel
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(Ink.secondary)
                TextField("How do I…?", text: $search.query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 15))
                    .foregroundColor(Ink.primary)
                    .focused($focused)
                    .onSubmit { learnSelected() }
                if search.loading { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.08)))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(focused ? 0.22 : 0.06), lineWidth: 1))

            if search.results.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(search.message ?? "Ask how to do something, like “file an auto claim”.")
                        .font(.system(size: 12)).foregroundColor(Ink.secondary)
                    HStack(spacing: 12) {
                        hint("↵", "learn it")
                        hint("⌘↵", "do it for me")
                        hint("esc", "close")
                    }
                }
                .padding(.top, 6)
                .padding(.horizontal, 4)
            } else {
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 2) {
                        ForEach(Array(search.results.prefix(5).enumerated()), id: \.element.id) { i, s in
                            ResultRow(skill: s, selected: i == model.selectedResult, replayEnabled: search.replayEnabled,
                                      onLearn: { search.onLearn(s) }, onRun: { search.onRun(s) })
                                .onHover { if $0 { model.selectedResult = i } }
                        }
                    }
                }
            }
            // ⌘↵ runs the selected skill.
            Button("") { runSelected() }
                .keyboardShortcut(.return, modifiers: .command)
                .opacity(0).frame(width: 0, height: 0)
                .accessibilityHidden(true)
        }
        .onAppear { focused = true }
        .onChange(of: model.focusSearchToken) { _ in focused = true }
        .onChange(of: search.query) { _ in model.selectedResult = 0 }
    }

    private func hint(_ key: String, _ text: String) -> some View {
        HStack(spacing: 4) {
            Text(key).font(.system(size: 10, design: .monospaced)).foregroundColor(Ink.secondary)
                .padding(.horizontal, 4).padding(.vertical, 1)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Ink.line))
            Text(text).font(.system(size: 11)).foregroundColor(Ink.secondary)
        }
    }

    private var selected: SkillSummary? {
        let r = search.results
        guard !r.isEmpty else { return nil }
        return r[min(model.selectedResult, r.count - 1)]
    }

    private func learnSelected() { if let s = selected { search.onLearn(s) } }
    private func runSelected() { if let s = selected, search.replayEnabled { search.onRun(s) } }
}

private struct ResultRow: View {
    let skill: SkillSummary
    let selected: Bool
    let replayEnabled: Bool
    let onLearn: () -> Void
    let onRun: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(skill.title).font(.system(size: 13, weight: .semibold)).foregroundColor(Ink.primary).lineLimit(1)
                Text(meta).font(.system(size: 11)).foregroundColor(Ink.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            Button("Learn", action: onLearn).buttonStyle(NotchButtonStyle(kind: .ghost))
            Button("Do it", action: onRun)
                .buttonStyle(NotchButtonStyle(kind: selected ? .light : .plain))
                .disabled(!replayEnabled)
                .help(replayEnabled ? "Run this skill on your Mac" : "Running skills is turned off by your admin")
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(selected ? 0.07 : 0)))
        .contentShape(Rectangle())
        .onTapGesture(perform: onLearn)
    }

    private var meta: String {
        var parts: [String] = []
        if let o = skill.owner?.name, !o.isEmpty { parts.append(o) }
        if let a = skill.apps.first { parts.append(a) }
        if let h = skill.health, let r = h.successRate, (h.runs ?? 0) > 0 { parts.append("\(Int((r * 100).rounded()))% runs succeed") }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Intent

private struct IntentNotchView: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TopRow(metrics: model.metrics) {
                LiveAvatar(dot: dot, size: 20, happy: true)
                Text("Saved · \(model.intentSteps) steps").font(.system(size: 12, weight: .semibold)).foregroundColor(Ink.primary)
            } trailing: {
                Text(countdown).font(.system(size: 11).monospacedDigit()).foregroundColor(Ink.secondary)
                    .accessibilityLabel("Saves without a note in \(model.intentSecondsLeft) seconds")
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("What did you just do?").font(.system(size: 15, weight: .semibold)).foregroundColor(Ink.primary)
                TextField(placeholder, text: $model.intentText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .foregroundColor(Ink.primary)
                    .focused($focused)
                    .onSubmit { model.finishIntent(model.intentText) }
                    .padding(.horizontal, 12)
                    .frame(height: 36)
                    .background(RoundedRectangle(cornerRadius: 11).fill(Color.white.opacity(0.08)))
                    .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.white.opacity(0.2), lineWidth: 1))
                HStack(spacing: 8) {
                    Text("Becomes the skill's title and helps leave out unrelated steps.")
                        .font(.system(size: 11)).foregroundColor(Ink.secondary).lineLimit(1)
                    Spacer(minLength: 6)
                    Button("Skip") { model.finishIntent(nil) }.buttonStyle(NotchButtonStyle(kind: .ghost))
                    Button("Save") { model.finishIntent(model.intentText) }.buttonStyle(NotchButtonStyle(kind: .light))
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 6)
        }
        .onAppear { focused = true }
        .onChange(of: model.focusIntentToken) { _ in focused = true }
    }

    private var placeholder: String {
        model.intentAppHint.isEmpty ? "e.g. Added a driver to an auto policy" : "e.g. Added a driver to an auto policy in \(model.intentAppHint)"
    }

    private var countdown: String {
        let s = max(model.intentSecondsLeft, 0)
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

// MARK: - Toast

private struct ToastNotchView: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel

    var body: some View {
        VStack(spacing: 0) {
            TopRow(metrics: model.metrics) {
                LiveAvatar(dot: dot, size: 20, happy: model.toastTone == .good)
            } trailing: {
                Image(systemName: icon).font(.system(size: 12, weight: .semibold)).foregroundColor(tint)
            }
            HStack(spacing: 6) {
                Text(model.toastTitle).font(.system(size: 12.5, weight: .semibold)).foregroundColor(Ink.primary).lineLimit(1)
                if !model.toastDetail.isEmpty {
                    Text(model.toastDetail).font(.system(size: 12)).foregroundColor(Ink.secondary).lineLimit(1)
                }
                Spacer(minLength: 6)
                if let title = model.toastActionTitle {
                    Button(title) { model.toastAction?(); model.requestMode(.idle) }
                        .buttonStyle(NotchButtonStyle(kind: .plain))
                }
            }
            .padding(.horizontal, 16)
            .frame(maxHeight: .infinity)
        }
        .accessibilityElement(children: .combine)
    }

    private var icon: String {
        switch model.toastTone {
        case .good: return "checkmark.circle.fill"
        case .info: return "info.circle.fill"
        case .attention: return "exclamationmark.triangle.fill"
        }
    }

    private var tint: Color {
        switch model.toastTone {
        case .good: return Ink.green
        case .info: return Ink.blue
        case .attention: return Ink.amber
        }
    }
}

// MARK: - Right-click menu

private struct NotchMenu: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var dot: DotModel

    var body: some View {
        Button(dot.recording ? "Stop recording" : "Start recording") { model.onRecordToggle() }
            .disabled(!dot.recording && (dot.replaying || !dot.recordingEnabled))
        Button("Search skills…") { model.requestMode(.open) }
        Button("Open library") { model.onLibrary() }
        Button("My recordings") { model.onRecordings() }
        Divider()
        Menu("Your dot") {
            ForEach(AvatarKind.allCases, id: \.self) { k in
                Button(action: { model.onChooseAvatar(k) }) {
                    if dot.avatar == k {
                        Label(k.displayName, systemImage: "checkmark")
                    } else {
                        Text(k.displayName)
                    }
                }
            }
        }
        Button("Use a floating dot instead") { model.onUseFloatingDot() }
        Button("Settings…") { model.onSettings() }
        Divider()
        Button("Quit Work Shadower") { NSApp.terminate(nil) }
    }
}
#endif
