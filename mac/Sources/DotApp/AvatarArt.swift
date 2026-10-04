#if os(macOS)
// The five dot characters, drawn natively so the floating dot matches the web app.
// All geometry is in the same 64×64 grid as web/src/components/Avatar.tsx.
import AppKit
import SwiftUI
import DotCore

/// Per-character colours and face placement (mirror of SPECS in Avatar.tsx).
struct AvatarStyle {
    let hi: Color, mid: Color, lo: Color
    let lx: CGFloat, rx: CGFloat, ey: CGFloat, my: CGFloat
    let eyeFill: Color, pupil: Color, mouth: Color, cheek: Color

    static func of(_ k: AvatarKind) -> AvatarStyle {
        switch k {
        case .orb:
            return AvatarStyle(hi: hex(0xa8a2ff), mid: hex(0x5246e6), lo: hex(0x2a2091),
                               lx: 23.5, rx: 40.5, ey: 35, my: 45.5,
                               eyeFill: .white, pupil: hex(0x1b1648), mouth: hex(0x1b1648), cheek: hex(0xff8fb8))
        case .sprout:
            return AvatarStyle(hi: hex(0xa6f0c4), mid: hex(0x34b57a), lo: hex(0x13704a),
                               lx: 24, rx: 40, ey: 37, my: 47.5,
                               eyeFill: .white, pupil: hex(0x0d3b28), mouth: hex(0x0d3b28), cheek: hex(0xff9db0))
        case .ember:
            return AvatarStyle(hi: hex(0xffe08a), mid: hex(0xff8a3d), lo: hex(0xc9361f),
                               lx: 24.5, rx: 39.5, ey: 41, my: 50.5,
                               eyeFill: .white, pupil: hex(0x4a1408), mouth: hex(0x4a1408), cheek: hex(0xff5f6d))
        case .nimbus:
            return AvatarStyle(hi: .white, mid: hex(0xd6e8ff), lo: hex(0x8fb5ee),
                               lx: 25.5, rx: 40.5, ey: 41.5, my: 50.5,
                               eyeFill: hex(0x24345e), pupil: .white, mouth: hex(0x24345e), cheek: hex(0xffa9c4))
        case .pixel:
            return AvatarStyle(hi: hex(0xd6c2ff), mid: hex(0x8a5cf6), lo: hex(0x4e25b8),
                               lx: 25, rx: 39, ey: 37, my: 46,
                               eyeFill: hex(0x8ff3ff), pupil: hex(0x0b2a44), mouth: hex(0x8ff3ff), cheek: hex(0xff8fd8))
        }
    }

    private static func hex(_ v: UInt32) -> Color {
        Color(red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255, blue: Double(v & 0xff) / 255)
    }
}

/// Maps the 64-grid into a rect.
private struct Grid {
    let r: CGRect
    func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
        CGPoint(x: r.minX + x * r.width / 64, y: r.minY + y * r.height / 64)
    }
    func s(_ v: CGFloat) -> CGFloat { v * r.width / 64 }
}

/// Body silhouette of a character.
struct AvatarBody: Shape {
    let kind: AvatarKind

    func path(in rect: CGRect) -> Path {
        let g = Grid(r: rect)
        var p = Path()
        switch kind {
        case .orb:
            p.addEllipse(in: CGRect(x: g.p(6, 9).x, y: g.p(6, 9).y, width: g.s(52), height: g.s(52)))
        case .sprout:
            p.move(to: g.p(32, 14))
            p.addCurve(to: g.p(57, 38), control1: g.p(48, 14), control2: g.p(57, 22))
            p.addCurve(to: g.p(32, 60), control1: g.p(57, 52), control2: g.p(47, 60))
            p.addCurve(to: g.p(7, 38), control1: g.p(17, 60), control2: g.p(7, 52))
            p.addCurve(to: g.p(32, 14), control1: g.p(7, 22), control2: g.p(16, 14))
            p.closeSubpath()
        case .ember:
            p.move(to: g.p(33, 6))
            p.addCurve(to: g.p(54, 41), control1: g.p(37, 15), control2: g.p(54, 23))
            p.addCurve(to: g.p(32, 61), control1: g.p(54, 53), control2: g.p(44, 61))
            p.addCurve(to: g.p(10, 41), control1: g.p(20, 61), control2: g.p(10, 53))
            p.addCurve(to: g.p(21, 18), control1: g.p(10, 31), control2: g.p(16, 25))
            p.addCurve(to: g.p(29, 27.5), control1: g.p(22.5, 23), control2: g.p(25.5, 26.5))
            p.addCurve(to: g.p(33, 6), control1: g.p(27.5, 20), control2: g.p(29.5, 12.5))
            p.closeSubpath()
        case .nimbus:
            p.move(to: g.p(17, 60))
            p.addCurve(to: g.p(4.5, 46), control1: g.p(8.5, 60), control2: g.p(3.5, 53.5))
            p.addCurve(to: g.p(17.5, 35.5), control1: g.p(5.5, 38.5), control2: g.p(11.5, 34.5))
            p.addCurve(to: g.p(34, 18.5), control1: g.p(17.5, 25.5), control2: g.p(25, 18.5))
            p.addCurve(to: g.p(50, 30.5), control1: g.p(42, 18.5), control2: g.p(48, 23.5))
            p.addCurve(to: g.p(60.5, 45), control1: g.p(57, 30.5), control2: g.p(61.5, 37))
            p.addCurve(to: g.p(46, 60), control1: g.p(59.5, 53.5), control2: g.p(53.5, 60))
            p.closeSubpath()
        case .pixel:
            p.addRoundedRect(in: CGRect(origin: g.p(9, 16), size: CGSize(width: g.s(46), height: g.s(44))),
                             cornerSize: CGSize(width: g.s(15), height: g.s(15)))
        }
        return p
    }
}

/// The smile: a filled open mouth that grows from a line (amount 0…1).
struct SmileShape: Shape {
    var my: CGFloat
    var amount: CGFloat
    var animatableData: CGFloat {
        get { amount }
        set { amount = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let g = Grid(r: rect)
        let half: CGFloat = 3 + 4.5 * amount
        let depth: CGFloat = 1.8 + 8.2 * amount
        var p = Path()
        p.move(to: g.p(32 - half, my - 1.5 * amount))
        p.addQuadCurve(to: g.p(32 + half, my - 1.5 * amount), control: g.p(32, my + depth))
        if amount > 0.15 { p.closeSubpath() }
        return p
    }
}

/// Character with live eyes and smile. `openness` 0…1, `smile` 0…1, `pupil` in points.
struct DotCharacter: View {
    let kind: AvatarKind
    let openness: CGFloat
    let smile: CGFloat
    let pupil: CGSize
    let glow: Color
    var size: CGFloat = 44

    var body: some View {
        let st = AvatarStyle.of(kind)
        let k = size / 64
        ZStack(alignment: .topLeading) {
            accessoriesBehind(st, k)
            AvatarBody(kind: kind)
                .fill(RadialGradient(colors: [st.hi, st.mid, st.lo],
                                     center: UnitPoint(x: 0.34, y: 0.3), startRadius: 1, endRadius: size * 0.75))
                .frame(width: size, height: size)
                .shadow(color: glow.opacity(0.55), radius: 8)
            if kind == .pixel {
                RoundedRectangle(cornerRadius: 11 * k)
                    .fill(Color(red: 0.09, green: 0.04, blue: 0.23).opacity(0.62))
                    .frame(width: 36 * k, height: 30 * k)
                    .offset(x: 14 * k, y: 24 * k)
            }
            // cheeks
            Group {
                Ellipse().fill(st.cheek).frame(width: 8 * k, height: 4.8 * k)
                    .offset(x: (st.lx - 9) * k, y: (st.ey + 4.6) * k)
                Ellipse().fill(st.cheek).frame(width: 8 * k, height: 4.8 * k)
                    .offset(x: (st.rx + 1) * k, y: (st.ey + 4.6) * k)
            }
            .opacity(Double(0.7 * smile))
            // eyes
            eye(st, k).offset(x: (st.lx - 5.4) * k, y: (st.ey - 7.6) * k)
            eye(st, k).offset(x: (st.rx - 5.4) * k, y: (st.ey - 7.6) * k)
            // mouth: a calm line at rest, a wide smile on hover
            SmileShape(my: st.my, amount: smile)
                .fill(smile > 0.15 ? st.mouth : Color.clear)
                .overlay(SmileShape(my: st.my, amount: smile)
                    .stroke(st.mouth, style: StrokeStyle(lineWidth: 2 * k, lineCap: .round))
                    .opacity(smile > 0.15 ? 0 : 1))
                .frame(width: size, height: size)
        }
        .frame(width: size, height: size)
    }

    @ViewBuilder
    private func accessoriesBehind(_ st: AvatarStyle, _ k: CGFloat) -> some View {
        switch kind {
        case .sprout:
            ZStack(alignment: .topLeading) {
                Ellipse().fill(Color(red: 0.37, green: 0.83, blue: 0.58))
                    .frame(width: 18 * k, height: 7 * k)
                    .rotationEffect(.degrees(25 - 12 * Double(smile)), anchor: .trailing)
                    .offset(x: 15 * k, y: 4 * k)
                Ellipse().fill(Color(red: 0.24, green: 0.73, blue: 0.47))
                    .frame(width: 17 * k, height: 7 * k)
                    .rotationEffect(.degrees(-28 + 12 * Double(smile)), anchor: .leading)
                    .offset(x: 33 * k, y: 1 * k)
            }
        case .pixel:
            ZStack(alignment: .topLeading) {
                Capsule().fill(Color(red: 0.23, green: 0.11, blue: 0.56))
                    .frame(width: 2.6 * k, height: 8 * k).offset(x: 30.7 * k, y: 8 * k)
                Circle().fill(Color(red: 1, green: 0.84 + 0.12 * Double(smile), blue: 0.3 + 0.4 * Double(smile)))
                    .frame(width: 8 * k, height: 8 * k).offset(x: 28 * k, y: 3 * k)
            }
        case .nimbus:
            ZStack(alignment: .topLeading) {
                Circle().fill(Color.white.opacity(0.9)).frame(width: 6.4 * k, height: 6.4 * k)
                    .offset(x: 52.8 * k, y: (16.8 - 3 * smile) * k)
                Circle().fill(Color.white.opacity(0.7)).frame(width: 3.8 * k, height: 3.8 * k)
                    .offset(x: 59 * k, y: (12 - 3 * smile) * k)
            }
        default:
            EmptyView()
        }
    }

    private func eye(_ st: AvatarStyle, _ k: CGFloat) -> some View {
        ZStack {
            st.eyeFill
            Circle()
                .fill(st.pupil)
                .frame(width: 5.8 * k, height: 5.8 * k)
                .offset(pupil)
                .opacity(Double(openness))
        }
        .frame(width: 10.8 * k, height: 13.2 * k)
        .mask(Eye(openness: openness))
    }
}

/// A small, static preview used by Settings → "Your dot".
struct AvatarSwatch: View {
    let kind: AvatarKind
    var happy = false
    var size: CGFloat = 40

    var body: some View {
        DotCharacter(kind: kind, openness: happy ? 1 : 0, smile: happy ? 1 : 0, pupil: .zero,
                     glow: .clear, size: size)
    }
}
#endif
