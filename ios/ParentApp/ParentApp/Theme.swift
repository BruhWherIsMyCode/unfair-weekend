import SwiftUI

/// Colours and sizes from the Claude Design "Client Call Video" (docs/design/export/unpacked/client-video.jsx).
/// The design frame is 440 x 956 pt. Fonts are the system font (SF Pro) only.
enum Palette {
    static let phone = Color(hex: 0x2A2622)
    static let amber = Color(hex: 0xF2B25C)
    static let green = Color(hex: 0x7FD08F)
    static let accept = Color(hex: 0x2BA246)
    static let decline = Color(hex: 0xD12A24)
    static let end = Color(hex: 0xD63B32)
    /// rgba(32,28,24,.7): the two pills at the top of the call.
    static let pill = Color(hex: 0x201C18, opacity: 0.7)
    /// rgba(32,28,24,.88): the card.
    static let card = Color(hex: 0x201C18, opacity: 0.88)
    static let stripeA = Color(hex: 0x3B3530)
    static let stripeB = Color(hex: 0x433C36)
    static let selfViewA = Color(hex: 0x5A5047)
    static let selfViewB = Color(hex: 0x655A50)
    /// linear-gradient(160deg, #2F2B42 0%, #2B2C3D 55%, #30395A 100%) on 440 x 956.
    static let ringGradient = LinearGradient(
        stops: [
            .init(color: Color(hex: 0x2F2B42), location: 0),
            .init(color: Color(hex: 0x2B2C3D), location: 0.55),
            .init(color: Color(hex: 0x30395A), location: 1),
        ],
        startPoint: UnitPoint(x: 0.092, y: -0.015),
        endPoint: UnitPoint(x: 0.908, y: 1.015)
    )
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }
}

/// The design's three curves. Nothing else animates with another curve, except the subtitle words
/// (ease-in-out sine, computed per frame) and the two loops (ringing pulse, Listening bars).
enum Motion {
    /// enter: ease-out cubic. Things appearing.
    static func enter(_ duration: Double = 0.6) -> Animation {
        .timingCurve(0.33, 1, 0.68, 1, duration: duration)
    }

    /// pop: ease-out back (overshoot 1.70158). The Done check mark.
    static func pop(_ duration: Double = 0.5) -> Animation {
        .timingCurve(0.34, 1.56, 0.64, 1, duration: duration)
    }

    /// glide: ease-in-out cubic. Crossfades, size changes, the avatar settle.
    static func glide(_ duration: Double = 1) -> Animation {
        .timingCurve(0.65, 0, 0.35, 1, duration: duration)
    }

    static func sineInOut(_ t: Double) -> Double {
        -(cos(Double.pi * t) - 1) / 2
    }

    static func clamp01(_ value: Double) -> Double {
        min(1, max(0, value))
    }
}

/// The handset glyph from the design (Material "call" path, 24 x 24), drawn as a shape.
/// The tiny 1-unit corner arcs are drawn as quadratic corners.
struct PhoneGlyph: Shape {
    func path(in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 24
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + x * s, y: rect.minY + y * s)
        }
        var path = Path()
        path.move(to: p(6.6, 10.8))
        path.addQuadCurve(to: p(13.2, 17.4), control: p(8.85, 15.15))
        path.addLine(to: p(15.4, 15.2))
        path.addQuadCurve(to: p(16.4, 14.95), control: p(15.8, 14.85))
        path.addCurve(to: p(20, 15.52), control1: p(17.5, 15.32), control2: p(18.7, 15.52))
        path.addQuadCurve(to: p(21, 16.52), control: p(21, 15.52))
        path.addLine(to: p(21, 20))
        path.addQuadCurve(to: p(20, 21), control: p(21, 21))
        path.addCurve(to: p(3, 4), control1: p(10.6, 21), control2: p(3, 13.4))
        path.addQuadCurve(to: p(4, 3), control: p(3, 3))
        path.addLine(to: p(7.5, 3))
        path.addQuadCurve(to: p(8.5, 4), control: p(8.5, 3))
        path.addCurve(to: p(9.07, 7.57), control1: p(8.5, 5.25), control2: p(8.7, 6.45))
        path.addQuadCurve(to: p(8.82, 8.57), control: p(9.25, 8.15))
        path.closeSubpath()
        return path
    }
}

struct PhoneIcon: View {
    let size: CGFloat
    var hangUp = false

    var body: some View {
        PhoneGlyph()
            .fill(Color.white)
            .frame(width: size, height: size)
            .rotationEffect(.degrees(hangUp ? 135 : 0))
    }
}

/// repeating-linear-gradient(135deg, a 0 period/2, b period/2 period): the design's placeholder stripes.
struct Stripes: View {
    let a: Color
    let b: Color
    let period: CGFloat

    var body: some View {
        Canvas { context, size in
            context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(a))
            let step = period * 2.squareRoot()
            let reach = size.width + size.height
            var c: CGFloat = 0
            while c < reach + step {
                let c1 = c + step / 2
                let c2 = c + step
                var band = Path()
                band.move(to: CGPoint(x: c1, y: 0))
                band.addLine(to: CGPoint(x: c2, y: 0))
                band.addLine(to: CGPoint(x: c2 - reach, y: reach))
                band.addLine(to: CGPoint(x: c1 - reach, y: reach))
                band.closeSubpath()
                context.fill(band, with: .color(b))
                c += step
            }
        }
    }
}
