#if os(macOS)
import AppKit
import ApplicationServices
import CoreGraphics
import DotCore

/// Runs a published skill on this Mac ("Do it for me").
///
/// Ladder per step:
///   1. deterministic — find the target in the AX tree (TargetMatcher) and act
///   2. llm_repair    — send a compact UI tree to /replay/repair, act if confidence ≥ 0.7,
///                      then propose the fix to the skill owner
///   3. human         — pause and ask the user to do the step, then click Done
///
/// Irreversible steps always wait for a confirm click, in every mode.
///
/// Threading: `start` is called on main. The step loop runs in a detached
/// task; every UI/NSWorkspace touch hops to main via `onMain`.
final class Replayer {
    private let api: APIClient
    private let hud = ReplayHUDController()
    private let inputsForm = InputsFormController()
    private let settings: SettingsStore

    /// Main-thread callbacks wired by AppController.
    var dotFrame: () -> NSRect? = { nil }
    var onStateChange: (Bool) -> Void = { _ in }
    var llmEnabled: () -> Bool = { false }

    private(set) var isRunning = false

    static let repairThreshold = 0.7
    static let verifyTimeout: TimeInterval = 5

    init(api: APIClient, settings: SettingsStore) {
        self.api = api
        self.settings = settings
    }

    // MARK: Entry

    /// Main thread. `version` nil = current published version.
    func start(skillID: String, version: Int?) {
        guard !isRunning else {
            NSSound.beep()
            return
        }
        isRunning = true
        onStateChange(true)
        let api = self.api
        Task {
            do {
                var v = version
                if v == nil {
                    let detail = try await api.skill(id: skillID)
                    v = detail.currentVersion
                }
                guard let ver = v, ver > 0 else {
                    throw ReplayError.message("This skill has no published version yet.")
                }
                let skill = try await api.skillVersion(id: skillID, version: ver)
                DispatchQueue.main.async { self.askInputs(skillID: skillID, skill: skill) }
            } catch {
                DispatchQueue.main.async { self.failEarly(Self.describe(error)) }
            }
        }
    }

    private func askInputs(skillID: String, skill: SkillVersion) {
        let inputs = Template.requiredInputs(for: skill.content)
        inputsForm.present(title: skill.content.title, inputs: inputs, defaultMode: settings.replayMode) { [weak self] result in
            guard let self = self else { return }
            guard let picked = result else {
                self.finishUI()
                return
            }
            let (values, mode) = picked
            self.settings.replayMode = mode
            self.begin(skillID: skillID, skill: skill, values: values, mode: mode)
        }
    }

    private func begin(skillID: String, skill: SkillVersion, values: [String: String], mode: RunMode) {
        let m = hud.model
        m.control.reset()
        m.skillTitle = skill.content.title
        m.stepCount = skill.content.runnableSteps.count
        m.stepNumber = 0
        m.stepTitle = ""
        m.instruction = ""
        m.paused = false
        m.modeText = mode == .guided ? "Guided: you confirm each step" : "Auto: I'll stop if something needs you"
        m.phase = .working("Starting…")
        hud.show(near: dotFrame())

        let api = self.api
        Task.detached { [weak self] in
            guard let self = self else { return }
            var runID: String?
            do {
                let created = try await api.createRun(
                    RunCreate(skillID: skillID, version: skill.version, mode: mode, inputs: values),
                    idempotencyKey: UUID().uuidString)
                runID = created.id
            } catch {
                // Telemetry is best effort: still run the skill.
                Log.replay.error("createRun failed: \(String(describing: error), privacy: .public)")
            }
            await self.runSteps(skillID: skillID, skill: skill, values: values, mode: mode, runID: runID)
        }
    }

    // MARK: Step loop (background)

    private func runSteps(skillID: String, skill: SkillVersion, values: [String: String],
                          mode: RunMode, runID: String?) async {
        let m = hud.model
        var outcome: RunOutcome = .succeeded
        var errorText: String?

        for (n, raw) in skill.content.runnableSteps.enumerated() {
            if m.control.stopped { outcome = .aborted; break }
            let step = Template.fill(step: raw, with: values)
            onMain {
                m.stepNumber = n + 1
                m.stepTitle = step.title
                m.instruction = step.instruction
                m.phase = .working("Working…")
            }
            await waitWhilePaused()
            if m.control.stopped { outcome = .aborted; break }

            let started = Date()
            let result = await perform(step: step, skillID: skillID, version: skill.version, mode: mode)
            let ms = Int(Date().timeIntervalSince(started) * 1000)

            if let runID = runID {
                let report = RunStepReport(stepIndex: step.index, status: result.status, strategy: result.strategy,
                                           durationMS: ms, detail: result.detail)
                try? await api.reportStep(runID: runID, report)
            }
            if result.stop { outcome = .aborted; break }
            if result.status == .failed {
                outcome = .failed
                errorText = "Step \(step.index) failed"
                break
            }
        }

        if let runID = runID {
            try? await api.finishRun(runID: runID, RunFinish(status: outcome, error: errorText))
        }
        let summary: String
        switch outcome {
        case .succeeded: summary = "Done — all steps finished."
        case .failed: summary = errorText ?? "Stopped on an error."
        case .aborted: summary = "Stopped."
        }
        onMain {
            m.phase = .finished(summary)
            self.isRunning = false
            self.onStateChange(false)
        }
    }

    private struct StepResult {
        var status: StepStatus
        var strategy: StepStrategy
        var detail: String?
        var stop = false
    }

    private func perform(step: SkillStep, skillID: String, version: Int, mode: RunMode) async -> StepResult {
        let m = hud.model

        // Gate: guided mode confirms every step; irreversible steps always confirm.
        if mode == .guided || step.irreversible {
            let choice = await m.ask(.confirm(irreversible: step.irreversible))
            switch choice {
            case .stop: return StepResult(status: .skipped, strategy: .human, detail: "stopped by user", stop: true)
            case .skip: return StepResult(status: .skipped, strategy: .human, detail: "skipped by user")
            case .done: return StepResult(status: .confirmed, strategy: .human, detail: "performed by user")
            case .proceed: break
            }
            onMain { m.phase = .working("Working…") }
        }

        guard let action = step.action, action.type != .unknown else {
            return await askHuman(step, reason: "I don't know how to do this one automatically. Please do it, then click Done.")
        }

        // Bring the right app forward first.
        if let appName = step.app, action.type != .openApp, action.type != .openURL {
            _ = activateApp(named: appName)
            try? await Task.sleep(nanoseconds: 400_000_000)
        }

        var strategy: StepStrategy = .deterministic
        var detail: String?
        switch action.type {
        case .openApp:
            let name = action.target?.label ?? step.app ?? ""
            guard !name.isEmpty, activateApp(named: name) || launchApp(named: name) else {
                return await askHuman(step, reason: "I couldn't open \(name.isEmpty ? "the app" : name). Please open it, then click Done.")
            }
            try? await Task.sleep(nanoseconds: 1_000_000_000)

        case .openURL:
            guard let s = action.url, let url = URL(string: s), url.scheme == "https" || url.scheme == "http" else {
                return await askHuman(step, reason: "The link for this step is missing. Please open the page, then click Done.")
            }
            onMain { _ = NSWorkspace.shared.open(url) }
            try? await Task.sleep(nanoseconds: 1_500_000_000)

        case .wait:
            try? await Task.sleep(nanoseconds: 1_500_000_000)

        case .key:
            guard let k = action.key, postKey(k) else {
                return await askHuman(step, reason: "I couldn't press that key combination. Please do it, then click Done.")
            }

        case .click, .menu, .type:
            guard let target = action.target, !target.isEmpty else {
                if action.type == .type, let text = action.text {
                    typeText(text)
                    break
                }
                return await askHuman(step, reason: "This step has no target I can find. Please do it, then click Done.")
            }
            let found = await locate(target: target, skillID: skillID, version: version, stepIndex: step.index,
                                     searchMenus: action.type == .menu)
            guard let hit = found else {
                return await askHuman(step, reason: "I couldn't find “\(target.label ?? "the control")” on screen. Please do this step, then click Done.")
            }
            strategy = hit.strategy
            detail = hit.strategy == .llmRepair ? "target repaired" : nil
            let ok: Bool
            if action.type == .type {
                ok = enter(text: action.text ?? "", into: hit.element)
            } else {
                ok = click(hit.element)
            }
            if !ok {
                return await askHuman(step, reason: "The control didn't respond. Please do this step, then click Done.")
            }

        case .unknown:
            return await askHuman(step, reason: "Please do this step, then click Done.")
        }

        // Verify the expected outcome.
        if let expect = step.expect, !expect.isEmpty {
            let ok = await verify(expect)
            if !ok {
                let r = await askHuman(step, reason: "I couldn't confirm this step worked. Check the screen: fix it if needed, then click Done.")
                return r.stop ? r : StepResult(status: .confirmed, strategy: .human, detail: "verification needed a human")
            }
        }
        return StepResult(status: strategy == .llmRepair ? .repaired : .ok, strategy: strategy, detail: detail)
    }

    private func askHuman(_ step: SkillStep, reason: String) async -> StepResult {
        let choice = await hud.model.ask(.human(reason))
        onMain { self.hud.model.phase = .working("Working…") }
        switch choice {
        case .done, .proceed: return StepResult(status: .confirmed, strategy: .human, detail: "performed by user")
        case .skip: return StepResult(status: .skipped, strategy: .human, detail: "skipped by user")
        case .stop: return StepResult(status: .skipped, strategy: .human, detail: "stopped by user", stop: true)
        }
    }

    private func waitWhilePaused() async {
        while hud.model.control.paused && !hud.model.control.stopped {
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
    }

    // MARK: Locating targets

    private struct Hit {
        let element: AXUIElement
        let strategy: StepStrategy
    }

    private func frontmostPID() -> pid_t? {
        return onMain { NSWorkspace.shared.frontmostApplication?.processIdentifier }
    }

    private func snapshotFrontmost(searchMenus: Bool) -> [AX.Node] {
        guard let pid = frontmostPID() else { return [] }
        AX.enableManualAccessibility(pid: pid)
        let root: AXUIElement
        if searchMenus {
            root = AX.app(pid: pid)
        } else {
            root = AX.focusedWindow(pid: pid) ?? AX.app(pid: pid)
        }
        return AX.snapshot(root: root, maxNodes: 2000, timeout: 1.5)
    }

    private func locate(target: Target, skillID: String, version: Int, stepIndex: Int,
                        searchMenus: Bool) async -> Hit? {
        // 1) Deterministic, with a couple of retries while the UI settles.
        var nodes: [AX.Node] = []
        for attempt in 0..<3 {
            nodes = snapshotFrontmost(searchMenus: searchMenus)
            if let match = TargetMatcher.best(target: target, nodes: nodes.map { $0.ui }) {
                return Hit(element: nodes[match.index].element, strategy: .deterministic)
            }
            if attempt < 2 { try? await Task.sleep(nanoseconds: 700_000_000) }
        }

        // 2) LLM repair (only when the server has an LLM configured).
        guard llmEnabled(), !nodes.isEmpty else { return nil }
        onMain { self.hud.model.phase = .working("Looking for a changed button…") }
        let tree = AX.compactTree(nodes, limit: 300)
        do {
            let resp = try await api.repair(RepairRequest(skillID: skillID, version: version,
                                                          stepIndex: stepIndex, uiTree: tree))
            guard let fixed = resp.target, resp.confidence >= Self.repairThreshold,
                  let match = TargetMatcher.best(target: fixed, nodes: nodes.map { $0.ui }, threshold: 0.8) else {
                return nil
            }
            let note = "Auto-repaired during replay (confidence \(String(format: "%.2f", resp.confidence)))"
            try? await api.suggestFix(skillID: skillID, SuggestFixRequest(stepIndex: stepIndex, newTarget: fixed, note: note))
            return Hit(element: nodes[match.index].element, strategy: .llmRepair)
        } catch {
            Log.replay.error("repair failed: \(String(describing: error), privacy: .public)")
            return nil
        }
    }

    // MARK: Verification

    private func verify(_ expect: StepExpect) async -> Bool {
        let deadline = Date().addingTimeInterval(Self.verifyTimeout)
        while Date() < deadline {
            if checkExpect(expect) { return true }
            try? await Task.sleep(nanoseconds: 400_000_000)
        }
        return checkExpect(expect)
    }

    private func checkExpect(_ expect: StepExpect) -> Bool {
        guard let pid = frontmostPID() else { return false }
        if let want = expect.windowTitleContains, !want.isEmpty {
            let title = AX.focusedWindow(pid: pid).flatMap { AX.string($0, "AXTitle") } ?? ""
            if !title.localizedCaseInsensitiveContains(want) { return false }
        }
        if let el = expect.elementPresent {
            let t = Target(role: el.role, label: el.label)
            let nodes = snapshotFrontmost(searchMenus: false).map { $0.ui }
            if TargetMatcher.best(target: t, nodes: nodes, threshold: 0.7) == nil { return false }
        }
        return true
    }

    // MARK: Actions

    private func activateApp(named name: String) -> Bool {
        return onMain {
            let apps = NSWorkspace.shared.runningApplications.filter {
                $0.localizedName?.caseInsensitiveCompare(name) == .orderedSame
            }
            guard let app = apps.first else { return false }
            if app.isActive { return true }
            return app.activate(options: [.activateIgnoringOtherApps])
        }
    }

    private func launchApp(named name: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-a", name]
        do {
            try p.run()
            p.waitUntilExit()
            return p.terminationStatus == 0
        } catch {
            return false
        }
    }

    /// AXPress first; fall back to a real mouse click at the element's centre.
    private func click(_ el: AXUIElement) -> Bool {
        if AX.press(el) { return true }
        guard let f = AX.frame(el), f.width > 0, f.height > 0 else { return false }
        let p = CGPoint(x: f.midX, y: f.midY)
        if let hud = onMain({ self.hud.cgFrame }), hud.contains(p) { return false }
        let src = CGEventSource(stateID: .hidSystemState)
        let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
        let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)
        down?.post(tap: .cghidEventTap)
        usleep(40_000)
        up?.post(tap: .cghidEventTap)
        return down != nil
    }

    private func enter(text: String, into el: AXUIElement) -> Bool {
        _ = AX.focus(el)
        if AX.setValue(el, text) { return true }
        // Some fields (web forms) ignore AXValue: click to focus, then type.
        _ = click(el)
        usleep(150_000)
        typeText(text)
        return true
    }

    private func typeText(_ text: String) {
        let src = CGEventSource(stateID: .hidSystemState)
        for ch in text {
            let utf16 = Array(String(ch).utf16)
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false) else { continue }
            utf16.withUnsafeBufferPointer { buf in
                down.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
                up.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
            }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            usleep(12_000)
        }
    }

    private func postKey(_ raw: String) -> Bool {
        guard let combo = KeyCombo.parse(raw), let code = KeyCodes.code(for: combo.key) else { return false }
        var flags: CGEventFlags = []
        if combo.cmd { flags.insert(.maskCommand) }
        if combo.ctrl { flags.insert(.maskControl) }
        if combo.alt { flags.insert(.maskAlternate) }
        if combo.shift { flags.insert(.maskShift) }
        let src = CGEventSource(stateID: .hidSystemState)
        guard let down = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: true),
              let up = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: false) else { return false }
        down.flags = flags
        up.flags = flags
        down.post(tap: .cghidEventTap)
        usleep(30_000)
        up.post(tap: .cghidEventTap)
        return true
    }

    // MARK: Helpers

    private func failEarly(_ message: String) {
        let m = hud.model
        m.skillTitle = "Couldn't start"
        m.stepCount = 0
        m.stepTitle = ""
        m.instruction = ""
        m.phase = .finished(message)
        hud.show(near: dotFrame())
        finishUI()
    }

    private func finishUI() {
        isRunning = false
        onStateChange(false)
    }

    static func describe(_ error: Error) -> String {
        if let e = error as? ReplayError, case .message(let s) = e { return s }
        if let e = error as? APIError {
            if e.isAuthFailure { return "Please sign in again (Settings…)." }
            if e.status == 404 { return "That skill doesn't exist or you can't see it." }
            return e.description
        }
        return "Something went wrong: \(error.localizedDescription)"
    }
}

enum ReplayError: Error {
    case message(String)
}

/// Runs `f` on the main thread and returns its value (safe from any thread).
func onMain<T>(_ f: () -> T) -> T {
    if Thread.isMainThread { return f() }
    return DispatchQueue.main.sync(execute: f)
}
#endif
