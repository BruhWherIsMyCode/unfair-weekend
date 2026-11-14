import SwiftUI

/// Colours copied from docs/design/export/unpacked/template.html.
enum Palette {
    static let page = Color(hex: 0xF6F3EE)
    static let ink = Color(hex: 0x1F1B16)
    static let secondary = Color(hex: 0x6B6359)
    static let faint = Color(hex: 0x9C9388)
    static let divider = Color(hex: 0xE3DDD3)
    static let back = Color(hex: 0x8A5A33)

    static let amberCard = Color(hex: 0xFCE8C4)
    static let amberInk = Color(hex: 0x5A3804)
    static let amberLabel = Color(hex: 0x8A5406)
    static let amberTime = Color(hex: 0x7A5212)
    static let amberOutline = Color(hex: 0xE7B866)
    static let amberDot = Color(hex: 0xD98A0B)

    static let usualPill = Color(hex: 0xE9F0E6)
    static let usualInk = Color(hex: 0x2F4A33)
    static let usualDot = Color(hex: 0x5D8A64)

    static let green = Color(hex: 0x237A3A)
    static let red = Color(hex: 0xC4362F)
    static let grey = Color(hex: 0xEAE3D9)

    static let callBg = Color(hex: 0x2B2622)
    static let callEnd = Color(hex: 0xE5484D)

    // Striped placeholders: (dark, light) pairs.
    static let avatarStripes = (Color(hex: 0xE3D8C8), Color(hex: 0xEEE6DA))
    static let avatarText = Color(hex: 0x7E7468)
    static let thumbStripes = (Color(hex: 0xEBD5AE), Color(hex: 0xF3E3C7))
    static let clipStripes = (Color(hex: 0xD9CCB8), Color(hex: 0xE4D9C8))
    static let clipLabel = Color(hex: 0x6B5E4D)
    /// Around an aspect-fit comparison clip (the light clip stripe).
    static let letterbox = Color(hex: 0xE4D9C8)
    static let callAvatarStripes = (Color(hex: 0x5A5047), Color(hex: 0x655A50))
    static let callAvatarText = Color(hex: 0xC9C1B5)
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}

/// CSS `repeating-linear-gradient(135deg, a 0 w, b w 2w)`: diagonal bands running bottom-left to top-right.
struct Stripes: View {
    var colors: (Color, Color)
    var band: CGFloat

    var body: some View {
        Canvas { ctx, size in
            ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(colors.1))
            // A band is the set of points where (x + y) / sqrt(2) lies in [2kw, 2kw + w).
            let step = band * 2 * 2.squareRoot()
            let width = band * 2.squareRoot()
            var c: CGFloat = 0
            let end = size.width + size.height
            while c < end {
                var p = Path()
                p.move(to: CGPoint(x: c, y: 0))
                p.addLine(to: CGPoint(x: c + width, y: 0))
                p.addLine(to: CGPoint(x: c + width - end, y: end))
                p.addLine(to: CGPoint(x: c - end, y: end))
                p.closeSubpath()
                ctx.fill(p, with: .color(colors.0))
                c += step
            }
        }
    }
}

/// Round avatar: the profile photo (or LocalAssets' fedya.jpg) if present, otherwise the design's striped placeholder.
struct Avatar: View {
    var size: CGFloat
    var label: String = ""
    var photo: UIImage? = nil
    var stripes = Palette.avatarStripes
    var textColor = Palette.avatarText
    var fontSize: CGFloat = 9

    var body: some View {
        ZStack {
            if let photo {
                Image(uiImage: photo).resizable().scaledToFill()
            } else {
                Stripes(colors: stripes, band: 5)
                Text(verbatim: label)
                    .font(.system(size: fontSize, design: .monospaced))
                    .foregroundStyle(textColor)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
    }
}

struct Dot: View {
    var color: Color
    var body: some View { Circle().fill(color).frame(width: 8, height: 8) }
}

/// "● NOT HIS USUAL" (or HER, from the profile)
struct NotUsualLabel: View {
    @Environment(DemoStore.self) private var store
    var body: some View {
        HStack(spacing: 8) {
            Dot(color: Palette.amberDot)
            Text(verbatim: store.pronoun.notUsualCaps)
                .font(.system(size: 13, weight: .bold))
                .tracking(0.6)
                .foregroundStyle(Palette.amberLabel)
        }
    }
}

/// Green "As usual" pill with the time under it.
struct AsUsualBadge: View {
    var time: String
    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 6) {
                Dot(color: Palette.usualDot)
                Text("As usual").font(.system(size: 15, weight: .semibold))
            }
            .foregroundStyle(Palette.usualInk)
            .padding(.horizontal, 10)
            .frame(height: 28)
            .background(Palette.usualPill, in: Capsule())
            Text(verbatim: time)
                .font(.system(size: 13).monospacedDigit())
                .foregroundStyle(Palette.faint)
        }
    }
}

/// The phone glyph from the design's buttons.
struct PhoneGlyph: Shape {
    func path(in rect: CGRect) -> Path {
        // SVG path "M6.6 10.8a15.1 ..." in a 24x24 box, simplified to the same silhouette.
        let s = min(rect.width, rect.height) / 24
        var p = Path()
        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * s, y: rect.minY + y * s) }
        p.move(to: pt(6.6, 10.8))
        p.addQuadCurve(to: pt(13.2, 17.4), control: pt(9.2, 15.4))
        p.addLine(to: pt(15.4, 15.2))
        p.addQuadCurve(to: pt(16.4, 14.95), control: pt(15.85, 14.8))
        p.addQuadCurve(to: pt(20, 15.52), control: pt(18.1, 15.52))
        p.addQuadCurve(to: pt(21, 16.52), control: pt(21, 15.52))
        p.addLine(to: pt(21, 20))
        p.addQuadCurve(to: pt(20, 21), control: pt(21, 21))
        p.addQuadCurve(to: pt(3, 4), control: pt(3, 21))
        p.addQuadCurve(to: pt(4, 3), control: pt(3, 3))
        p.addLine(to: pt(7.5, 3))
        p.addQuadCurve(to: pt(8.5, 4), control: pt(8.5, 3))
        p.addQuadCurve(to: pt(9.07, 7.57), control: pt(8.5, 5.9))
        p.addQuadCurve(to: pt(8.82, 8.57), control: pt(9.2, 8.1))
        p.closeSubpath()
        return p
    }
}

enum Clock {
    private static let hm: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.dateFormat = "HH:mm"
        return f
    }()
    private static let day: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.setLocalizedDateFormatFromTemplate("EEEEdMMMM")
        return f
    }()
    private static let shortDayFormat: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.setLocalizedDateFormatFromTemplate("EEEdMMM")
        return f
    }()
    static func time(_ d: Date) -> String { hm.string(from: d) }
    /// "Fri 13 Nov"
    static func shortDay(_ d: Date) -> String { shortDayFormat.string(from: d).replacingOccurrences(of: ",", with: "") }
    /// "Thursday 12 November" (en_GB puts no comma here; strip one if a future OS adds it).
    static func date(_ d: Date) -> String { day.string(from: d).replacingOccurrences(of: ",", with: "") }
}
