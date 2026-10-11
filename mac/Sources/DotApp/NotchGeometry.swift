#if os(macOS)
// Where the notch is, and how big each state of the Work Shadower notch is.
import AppKit

/// The hardware notch (or, on displays without one, a stand-in "lip" at the top centre).
struct NotchMetrics: Equatable {
    /// The screen the notch lives on.
    var screenFrame: CGRect
    /// True on MacBooks with a camera housing; false on external / older displays.
    var hasNotch: Bool
    /// Size of the hardware notch, or of the stand-in on displays without one.
    var notchSize: CGSize
    /// Height of the menu bar row (content below this line is free to use).
    var menuBarHeight: CGFloat
    /// True when the menu bar is hidden (a full-screen app, or auto-hide).
    var menuBarHidden: Bool

    /// The screen the notch should live on: the built-in display with a notch if there is one,
    /// otherwise the primary display (the one with the menu bar).
    static func preferredScreen() -> NSScreen? {
        if let notched = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) { return notched }
        return NSScreen.screens.first ?? NSScreen.main
    }

    static func measure(_ screen: NSScreen) -> NotchMetrics {
        let f = screen.frame
        let menuBar = max(f.maxY - screen.visibleFrame.maxY, 0)
        let hidden = menuBar < 1
        if screen.safeAreaInsets.top > 0,
           let left = screen.auxiliaryTopLeftArea,
           let right = screen.auxiliaryTopRightArea {
            let w = max(f.width - left.width - right.width, 120)
            let h = screen.safeAreaInsets.top
            return NotchMetrics(screenFrame: f, hasNotch: true, notchSize: CGSize(width: w, height: h),
                                menuBarHeight: max(h, menuBar), menuBarHidden: hidden)
        }
        // No hardware notch: a slim stand-in the width of a typical notch.
        let h = hidden ? 24 : max(menuBar, 24)
        return NotchMetrics(screenFrame: f, hasNotch: false, notchSize: CGSize(width: 180, height: h),
                            menuBarHeight: h, menuBarHidden: hidden)
    }
}

/// The states of the notch, from quietest to most present.
enum NotchMode: Equatable {
    /// Exactly the hardware notch (plus status "ears" while something is happening).
    case idle
    /// Pointer resting on the notch: the three things people do most, or the live activity's controls.
    case peek
    /// Clicked, or ⌥⌘Space: ask "How do I…?", record, open the library.
    case open
    /// Just stopped recording: "What did you just do?"
    case intent
    /// A short confirmation that goes away on its own.
    case toast
}

enum NotchLayout {
    /// The window is a fixed transparent canvas; the black shape inside it changes size.
    static let canvas = CGSize(width: 760, height: 340)
    /// Width of each status "ear" on either side of the hardware notch.
    static let ear: CGFloat = 66

    static func size(_ mode: NotchMode, metrics m: NotchMetrics, hasStatus: Bool) -> CGSize {
        let n = m.notchSize
        switch mode {
        case .idle:
            if hasStatus { return CGSize(width: n.width + ear * 2, height: n.height) }
            // Without a hardware notch, idle is a slim lip so it doesn't block the menu bar.
            return m.hasNotch ? n : CGSize(width: 132, height: 6)
        case .peek:
            return CGSize(width: max(n.width + 230, 460), height: m.menuBarHeight + 62)
        case .open:
            return CGSize(width: 680, height: m.menuBarHeight + 214)
        case .intent:
            return CGSize(width: 560, height: m.menuBarHeight + 124)
        case .toast:
            return CGSize(width: max(n.width + 210, 420), height: m.menuBarHeight + 40)
        }
    }

    static func bottomRadius(_ mode: NotchMode, metrics m: NotchMetrics) -> CGFloat {
        switch mode {
        case .idle: return m.hasNotch ? min(m.notchSize.height * 0.32, 11) : 3
        case .peek, .toast: return 20
        case .open, .intent: return 26
        }
    }
}
#endif
