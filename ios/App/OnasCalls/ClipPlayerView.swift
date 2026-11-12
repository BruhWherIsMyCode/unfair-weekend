import AVFoundation
import SwiftUI

/// Fedya's clip and photo. Private files copied from ios/App/LocalAssets at build time, never in git.
/// Every caller copes with them being missing.
enum LocalAssets {
    static let clipURL = Bundle.main.url(forResource: "clip", withExtension: "mp4")
    static let fedyaPhoto: UIImage? = Bundle.main.path(forResource: "fedya", ofType: "jpg").flatMap(UIImage.init(contentsOfFile:))

    static func clipThumbnail() async -> UIImage? {
        guard let clipURL else { return nil }
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: clipURL))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 400, height: 400)
        guard let (image, _) = try? await generator.image(at: CMTime(seconds: 1, preferredTimescale: 600)) else { return nil }
        return UIImage(cgImage: image)
    }
}

/// Step 4 clip area: plays on appear, loops, muted by default. Tap toggles play / pause.
struct ClipPlayerView: View {
    var height: CGFloat = 250
    @State private var model = ClipModel()

    var body: some View {
        ZStack(alignment: .bottom) {
            if let player = model.player {
                PlayerLayerView(player: player)
            } else {
                Stripes(colors: Palette.clipStripes, band: 8)
            }
            controls
        }
        .overlay(alignment: .topLeading) {
            Text("clip, 2nd try")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(model.player == nil ? Palette.clipLabel : .white)
                .padding(.leading, 16)
                .padding(.top, 14)
        }
        .overlay(alignment: .topTrailing) {
            if model.player != nil {
                Button { model.toggleMute() } label: {
                    Image(systemName: model.muted ? "speaker.slash.fill" : "speaker.wave.2.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 32, height: 32)
                        .background(Color(hex: 0x1F1B16, opacity: 0.55), in: Circle())
                }
                .padding(10)
                .accessibilityLabel(model.muted ? Text("Sound on") : Text("Sound off"))
            }
        }
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 22))
        .contentShape(Rectangle())
        .onTapGesture { model.togglePlay() }
        .onAppear { model.start() }
        .onDisappear { model.stop() }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Group {
                if model.playing {
                    HStack(spacing: 4) {
                        RoundedRectangle(cornerRadius: 1).frame(width: 4, height: 16)
                        RoundedRectangle(cornerRadius: 1).frame(width: 4, height: 16)
                    }
                } else {
                    Image(systemName: "play.fill").font(.system(size: 14))
                }
            }
            .frame(width: 12, height: 16)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.35))
                    Capsule().fill(Color.white).frame(width: geo.size.width * model.progress)
                }
            }
            .frame(height: 4)
            Text(verbatim: "\(ClipModel.format(model.elapsed)) / \(ClipModel.format(model.duration))")
                .font(.system(size: 13, weight: .semibold).monospacedDigit())
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color(hex: 0x1F1B16, opacity: 0.55))
    }
}

@MainActor
@Observable
final class ClipModel {
    private(set) var player: AVQueuePlayer?
    private(set) var playing = false
    private(set) var muted = true
    private(set) var elapsed: Double = 0
    private(set) var duration: Double = 10

    @ObservationIgnored private var looper: AVPlayerLooper?
    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var placeholderTick: Task<Void, Never>?

    var progress: Double { duration > 0 ? min(1, elapsed / duration) : 0 }

    init() {
        guard let url = LocalAssets.clipURL else { return }
        let item = AVPlayerItem(url: url)
        let queue = AVQueuePlayer()
        queue.isMuted = true
        looper = AVPlayerLooper(player: queue, templateItem: item)
        player = queue
        Task { [weak self] in
            if let d = try? await AVURLAsset(url: url).load(.duration), d.seconds.isFinite, d.seconds > 0 {
                self?.duration = d.seconds.rounded()
            }
        }
    }

    func start() {
        // Clip sound must not stop because the ring/silent switch is on.
        try? AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
        if let player {
            if timeObserver == nil {
                timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.2, preferredTimescale: 600), queue: .main) { [weak self] t in
                    MainActor.assumeIsolated { self?.elapsed = t.seconds.isFinite ? t.seconds : 0 }
                }
            }
            player.play()
        } else {
            startPlaceholderTick()
        }
        playing = true
    }

    func stop() {
        player?.pause()
        placeholderTick?.cancel()
        playing = false
    }

    func togglePlay() {
        if playing { stop() } else { start() }
    }

    func toggleMute() {
        muted.toggle()
        player?.isMuted = muted
    }

    /// No clip file: count 0:00 to 0:10 and loop, like the design.
    private func startPlaceholderTick() {
        placeholderTick?.cancel()
        placeholderTick = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                self.elapsed = self.elapsed >= self.duration ? 0 : self.elapsed + 1
            }
        }
    }

    static func format(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

private struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerUIView {
        let v = PlayerUIView()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspectFill
        return v
    }

    func updateUIView(_ uiView: PlayerUIView, context: Context) {}

    final class PlayerUIView: UIView {
        override static var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}
