import SwiftUI

/// The real alert clip: her camera frames from both tries, played in a loop at the rate they were taken.
/// Tap pauses. While the frames download, or if the backend has none, the design's stripes with a plain label;
/// never the staged clip, which is not her.
struct FrameClipView: View {
    let clip: AlertClip?
    var height: CGFloat = 250
    @State private var paused = false
    @State private var pausedAt = 0

    var body: some View {
        ZStack {
            if let clip {
                TimelineView(.periodic(from: .now, by: Double(clip.periodMs) / 1000)) { context in
                    let index = paused ? pausedAt : Self.index(at: context.date, clip: clip)
                    Image(uiImage: clip.frames[index])
                        .resizable()
                        .scaledToFill()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .clipped()
                        .onTapGesture {
                            pausedAt = index
                            paused.toggle()
                        }
                }
            } else {
                Stripes(colors: Palette.clipStripes, band: 8)
            }
        }
        .overlay(alignment: .topLeading) {
            Text(clip == nil ? "clip loading or not kept" : "clip, both tries")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(clip == nil ? Palette.clipLabel : .white)
                .padding(.leading, 16)
                .padding(.top, 14)
        }
        .overlay(alignment: .center) {
            if paused {
                Image(systemName: "play.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(.white)
                    .frame(width: 56, height: 56)
                    .background(Color(hex: 0x1F1B16, opacity: 0.55), in: Circle())
                    .allowsHitTesting(false)
            }
        }
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 22))
    }

    private static func index(at date: Date, clip: AlertClip) -> Int {
        let step = Int(date.timeIntervalSinceReferenceDate * 1000) / max(clip.periodMs, 1)
        return step % clip.frames.count
    }
}
