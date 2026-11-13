import ParentCallKit
import SwiftUI

/// Flex-wrap for subtitle words: column gap 9, row gap 4. Every word is laid out from the start,
/// so nothing reflows while the words fade in.
struct WordFlow: Layout {
    var columnGap: CGFloat = 9
    var rowGap: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let placed = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        return placed.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let placed = arrange(width: bounds.width, subviews: subviews)
        for (index, origin) in placed.origins.enumerated() {
            subviews[index].place(at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y),
                                  proposal: .unspecified)
        }
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> (origins: [CGPoint], size: CGSize) {
        var origins: [CGPoint] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                y += rowHeight + rowGap
                x = 0
                rowHeight = 0
            }
            origins.append(CGPoint(x: x, y: y))
            x += size.width + columnGap
            rowHeight = max(rowHeight, size.height)
            widest = max(widest, x - columnGap)
        }
        return (origins, CGSize(width: widest, height: y + rowHeight))
    }
}

/// Subtitles: word i fades 0 -> 1 over 0.7 s (ease-in-out sine) from `subtitle.wordStart(i)`.
/// 32 pt bold; steps down only when a long line would not fit the card.
struct Subtitles: View {
    let label: String
    let subtitle: CallScreen.Subtitle

    var body: some View {
        TimelineView(.animation) { timeline in
            ViewThatFits(in: .vertical) {
                ForEach([32.0, 28, 25, 22, 19], id: \.self) { size in
                    column(size: size, now: timeline.date)
                }
            }
        }
    }

    private func column(size: CGFloat, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            CardLabel(text: label)
            WordFlow(columnGap: 9 * size / 32, rowGap: 4) {
                ForEach(Array(subtitle.words.enumerated()), id: \.offset) { index, word in
                    Text(word)
                        .font(.system(size: size, weight: .bold))
                        .tracking(-0.5 * size / 32)
                        .opacity(opacity(index, now))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func opacity(_ index: Int, _ now: Date) -> Double {
        let elapsed = now.timeIntervalSince(subtitle.wordStart(index))
        return Motion.sineInOut(Motion.clamp01(elapsed / 0.7))
    }
}

struct CardLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(Color.white.opacity(0.6))
    }
}

/// "Listening": five green bars in a 96 pt green-tinted circle. One of the two loops.
struct ListeningIndicator: View {
    var body: some View {
        TimelineView(.animation) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            VStack(spacing: 6) {
                HStack(alignment: .center, spacing: 4) {
                    ForEach(0..<5, id: \.self) { i in
                        Capsule()
                            .fill(Palette.green)
                            .frame(width: 5, height: 10 + 18 * (0.5 + 0.5 * sin(t * 7 + Double(i) * 1.3)))
                    }
                }
                .frame(height: 30)
                Text("Listening")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.green)
            }
            .frame(width: 96, height: 96)
            .background(Circle().fill(Palette.green.opacity(0.16)))
        }
    }
}

/// The task timer: track at 18% white, amber arc of the time left, number in the middle.
struct TimerRing: View {
    let timer: CallScreen.Timer

    var body: some View {
        switch timer {
        case .hidden:
            // Same space as the ring, so the text does not reflow when it appears.
            Color.clear.frame(width: 96, height: 96)
        case .running(let endsAt, let total):
            TimelineView(.animation) { timeline in
                let remaining = max(0, endsAt.timeIntervalSince(timeline.date))
                let progress = total > 0 ? min(1, remaining / total) : 0
                ring(progress: progress, color: Palette.amber) { number(Int(remaining.rounded(.up))) }
            }
        case .finished:
            ring(progress: 1, color: Palette.green) {
                CheckMark()
                    .stroke(Palette.green, style: StrokeStyle(lineWidth: 3 * 40 / 24, lineCap: .round, lineJoin: .round))
                    .frame(width: 40, height: 40)
            }
        }
    }

    private func ring<Center: View>(progress: Double, color: Color, @ViewBuilder center: () -> Center) -> some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 7)
            Circle()
                .trim(from: 0, to: progress)
                .stroke(color, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                .rotationEffect(.degrees(-90))
            center()
        }
        .frame(width: 84, height: 84)
        .frame(width: 96, height: 96)
    }

    private func number(_ value: Int) -> some View {
        Text("\(value)")
            .font(.system(size: 40, weight: .bold))
            .monospacedDigit()
            .foregroundStyle(.white)
    }
}

/// polyline 5,12.5 10,17 19,7 in a 24 x 24 box.
struct CheckMark: Shape {
    func path(in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 24
        var path = Path()
        path.move(to: CGPoint(x: rect.minX + 5 * s, y: rect.minY + 12.5 * s))
        path.addLine(to: CGPoint(x: rect.minX + 10 * s, y: rect.minY + 17 * s))
        path.addLine(to: CGPoint(x: rect.minX + 19 * s, y: rect.minY + 7 * s))
        return path
    }
}
