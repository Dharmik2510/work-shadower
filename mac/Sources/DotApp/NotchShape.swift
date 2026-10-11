#if os(macOS)
import SwiftUI

/// The notch silhouette: flat top flush with the screen edge, small concave "flares" where it
/// meets the top edge (like the hardware notch), and rounded bottom corners. Width, height and
/// corner radius animate together, so every state change reads as the notch itself growing.
struct NotchShape: Shape {
    var width: CGFloat
    var height: CGFloat
    var radius: CGFloat
    /// Size of the concave flare at each top corner.
    var flare: CGFloat = 8

    var animatableData: AnimatablePair<AnimatablePair<CGFloat, CGFloat>, CGFloat> {
        get { AnimatablePair(AnimatablePair(width, height), radius) }
        set {
            width = newValue.first.first
            height = newValue.first.second
            radius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let w = max(width, 1), h = max(height, 1)
        let x0 = rect.midX - w / 2, x1 = rect.midX + w / 2, top = rect.minY
        let r = min(radius, h / 2, w / 2)
        let f = min(flare, h / 2)
        var p = Path()
        p.move(to: CGPoint(x: x0 - f, y: top))
        p.addQuadCurve(to: CGPoint(x: x0, y: top + f), control: CGPoint(x: x0, y: top))
        p.addLine(to: CGPoint(x: x0, y: top + h - r))
        p.addQuadCurve(to: CGPoint(x: x0 + r, y: top + h), control: CGPoint(x: x0, y: top + h))
        p.addLine(to: CGPoint(x: x1 - r, y: top + h))
        p.addQuadCurve(to: CGPoint(x: x1, y: top + h - r), control: CGPoint(x: x1, y: top + h))
        p.addLine(to: CGPoint(x: x1, y: top + f))
        p.addQuadCurve(to: CGPoint(x: x1 + f, y: top), control: CGPoint(x: x1, y: top))
        p.closeSubpath()
        return p
    }
}
#endif
