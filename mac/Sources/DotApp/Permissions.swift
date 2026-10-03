#if os(macOS)
import AppKit
import ApplicationServices
import CoreGraphics

/// TCC permissions Dot needs.
///  - Accessibility: read the AX tree (recorder) and press/post events (replayer).
///  - Input Monitoring: listen-only keyboard event tap (recorder typing).
///  - Screen Recording: key-moment screenshots (optional; skipped when missing).
enum Permission: String, CaseIterable, Identifiable {
    case accessibility
    case inputMonitoring
    case screenRecording

    var id: String { return rawValue }

    var title: String {
        switch self {
        case .accessibility: return "Accessibility"
        case .inputMonitoring: return "Input Monitoring"
        case .screenRecording: return "Screen Recording"
        }
    }

    var why: String {
        switch self {
        case .accessibility: return "Read button and field names while recording; press them during replay."
        case .inputMonitoring: return "Notice keystrokes and shortcuts while recording (never in password fields)."
        case .screenRecording: return "Optional: a few screenshots of the front window at key moments."
        }
    }

    var required: Bool { return self != .screenRecording }

    var isGranted: Bool {
        switch self {
        case .accessibility: return AXIsProcessTrusted()
        case .inputMonitoring: return CGPreflightListenEventAccess()
        case .screenRecording: return CGPreflightScreenCaptureAccess()
        }
    }

    /// Triggers the system prompt (once per app identity; later calls are no-ops).
    func request() {
        switch self {
        case .accessibility:
            // Literal key avoids differences in how kAXTrustedCheckOptionPrompt is imported.
            let opts = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
        case .inputMonitoring:
            _ = CGRequestListenEventAccess()
        case .screenRecording:
            _ = CGRequestScreenCaptureAccess()
        }
    }

    var settingsURL: URL? {
        let anchor: String
        switch self {
        case .accessibility: anchor = "Privacy_Accessibility"
        case .inputMonitoring: anchor = "Privacy_ListenEvent"
        case .screenRecording: anchor = "Privacy_ScreenCapture"
        }
        return URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)")
    }

    func openSystemSettings() {
        if let url = settingsURL { NSWorkspace.shared.open(url) }
    }

    static var allRequiredGranted: Bool {
        return Permission.allCases.filter { $0.required }.allSatisfy { $0.isGranted }
    }
}
#endif
