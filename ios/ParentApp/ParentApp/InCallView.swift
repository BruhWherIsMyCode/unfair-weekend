import AVFoundation
import LiveKit
import ParentCallKit
import SwiftUI

/// Design screens 3 to 8: the live call. Everything here is drawn from `screen`; nothing is timed.
struct InCallView: View {
    let screen: CallScreen
    let title: String
    let camera: SelfViewCamera
    let clips: AVPlayer?
    let live: LiveAvatarFeed?
    let onEnd: () -> Void

    /// Avatar settle 1.08 -> 1.0 and the late fade-in of self-view and End.
    @State private var settled = false
    @State private var controlsShown = false

    var body: some View {
        ZStack {
            AvatarBackground(clips: clips, live: live)
                .scaleEffect(settled ? 1 : 1.08)
                .ignoresSafeArea()

            topBar
                .padding(.top, 76)
                .padding(.horizontal, 20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                .opacity(screen.card == nil ? 0 : 1)

            card
                .frame(height: 220)
                .padding(.horizontal, 16)
                .padding(.bottom, 300 + (screen.selfViewLarge ? 44 : 0))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                .opacity(screen.card == nil ? 0 : 1)

            selfView
                .padding(.leading, 20)
                .padding(.bottom, 56)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                .opacity(controlsShown ? 1 : 0)

            endButton
                .padding(.trailing, 28)
                .padding(.bottom, 64)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                .opacity(controlsShown ? 1 : 0)
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
        .animation(Motion.enter(0.7), value: screen.card == nil)
        .animation(Motion.glide(0.8), value: screen.selfViewLarge)
        .onAppear {
            withAnimation(Motion.glide(0.7)) { settled = true }
            withAnimation(Motion.enter(0.7).delay(0.3)) { controlsShown = true }
        }
    }

    // MARK: top bar

    private var topBar: some View {
        HStack {
            HStack(spacing: 10) {
                Circle().fill(Palette.amber).frame(width: 10, height: 10)
                Text(title).font(.system(size: 17, weight: .semibold))
            }
            .padding(.horizontal, 18)
            .frame(height: 44)
            .background(Capsule().fill(Palette.pill))

            Spacer()

            HStack(spacing: 6) {
                ForEach(CallScreen.Stage.allCases, id: \.self) { stage in
                    let state = screen.dot(stage)
                    Capsule()
                        .fill(dotColor(state))
                        .frame(width: state == .current ? 28 : 10, height: 10)
                }
            }
            .padding(.horizontal, 16)
            .frame(height: 44)
            .background(Capsule().fill(Palette.pill))
            .animation(Motion.glide(0.5), value: screen.stage)
        }
    }

    private func dotColor(_ state: CallScreen.DotState) -> Color {
        switch state {
        case .done: return Palette.green
        case .current: return Palette.amber
        case .upcoming: return Color.white.opacity(0.3)
        }
    }

    // MARK: card

    private var card: some View {
        ZStack {
            if let content = screen.card {
                CardContent(card: content)
                    .id(CardContent.identity(content))
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(RoundedRectangle(cornerRadius: 30, style: .continuous).fill(Palette.card))
        .clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
        .animation(Motion.glide(0.5), value: screen.card.map(CardContent.identity))
    }

    // MARK: self-view and End

    private var selfView: some View {
        let large = screen.selfViewLarge
        return CameraPreview(camera: camera)
            .background(Stripes(a: Palette.selfViewA, b: Palette.selfViewB, period: 16))
            .frame(width: large ? 200 : 150, height: large ? 266 : 216)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 25, style: .continuous)
                    .stroke(Color.white.opacity(0.25), lineWidth: 2)
                    .padding(-1)
            )
    }

    private var endButton: some View {
        Button(action: onEnd) {
            VStack(spacing: 10) {
                Circle()
                    .fill(Palette.end)
                    .frame(width: 96, height: 96)
                    .overlay(PhoneIcon(size: 40, hangUp: true))
                Text("End")
                    .font(.system(size: 20, weight: .semibold))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("End call")
    }
}

/// What the card shows for each `CallScreen.Card`. Fixed task texts live here; the sentence comes
/// from the backend.
struct CardContent: View {
    let card: CallScreen.Card

    /// The card crossfades whenever this changes: line id, question number or task name.
    static func identity(_ card: CallScreen.Card) -> String {
        switch card {
        case .assistant(let subtitle): return "assistant:\(subtitle.lineId)"
        case .question(let number, _, _, _): return "question:\(number)"
        case .task(_, _, let task, _, _): return "task:\(task)"
        case .sentence: return "task:sentence"
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 18) {
            switch card {
            case .assistant(let subtitle):
                Subtitles(label: "Assistant", subtitle: subtitle)
            case .question(let number, let total, let subtitle, let listening):
                Subtitles(label: "Question \(number) of \(total)", subtitle: subtitle)
                listeningSlot(listening)
            case .task(let step, let total, let task, let timer, let hint):
                VStack(alignment: .leading, spacing: 8) {
                    CardLabel(text: "Step \(step) of \(total)")
                    Text(Self.title(task))
                        .font(.system(size: 34, weight: .bold))
                        .tracking(-0.6)
                        .lineLimit(2)
                        .minimumScaleFactor(0.7)
                    subline(Self.hintText(hint) ?? Self.instruction(task))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                TimerRing(timer: timer)
            case .sentence(let step, let total, let sentence, let listening):
                VStack(alignment: .leading, spacing: 8) {
                    CardLabel(text: "Step \(step) of \(total)")
                    Text("\u{201C}\(sentence)\u{201D}")
                        .font(.system(size: 30, weight: .bold))
                        .tracking(-0.5)
                        .lineLimit(3)
                        .minimumScaleFactor(0.6)
                    subline("Say it out loud")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                listeningSlot(listening)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// The space is kept even when not listening, so the text never reflows when it appears.
    private func listeningSlot(_ listening: Bool) -> some View {
        ZStack {
            if listening {
                ListeningIndicator().transition(.opacity)
            }
        }
        .frame(width: 96, height: 96)
        .animation(Motion.enter(0.5), value: listening)
    }

    private func subline(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 21))
            .foregroundStyle(Color.white.opacity(0.8))
            .lineLimit(1)
            .minimumScaleFactor(0.8)
    }

    static func title(_ task: String) -> String {
        switch task {
        case "smile": return "Smile wide"
        case "arms": return "Raise one hand"
        default: return "Follow the voice"
        }
    }

    static func instruction(_ task: String) -> String {
        switch task {
        case "smile": return "Relax, then smile when asked"
        case "arms": return "Left hand first, then swap"
        default: return "Look at the screen"
        }
    }

    /// Neutral framing hints from the backend. Unknown codes show nothing.
    static func hintText(_ hint: String?) -> String? {
        switch hint {
        case "look_at_camera": return "Look at the screen"
        case "show_your_hand": return "Show your hand"
        case "show_both_arms": return "Show your hand"
        case "check_camera": return "Check your camera"
        default: return nil
        }
    }
}

/// The avatar, framed like the other side of a video call. The live Simli stream when connected, otherwise the
/// backend's prepared clip for the current line (holding its last frame between lines). Before either, a local
/// loop or still from ios/ParentApp/LocalAssets (git-ignored), else the design's striped placeholder.
///
/// Simli video is square (768 x 768), the face filling about half of it. It is shown at the screen's width, not
/// stretched to the screen's height (which made the face about 2.7 times larger than a real call), over a
/// blurred, filled copy of itself so the rest of the screen matches it.
struct AvatarBackground: View {
    let clips: AVPlayer?
    let live: LiveAvatarFeed?

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            let frame = Self.avatarFrame(in: size)
            ZStack(alignment: .topLeading) {
                placeholder
                    .frame(width: size.width, height: size.height)
                    .clipped()
                if let clips {
                    framed(size: size, frame: frame,
                           backdrop: ClipLayer(player: clips, blurred: true),
                           video: ClipLayer(player: clips, blurred: false))
                }
                if let live, let track = live.track {
                    framed(size: size, frame: frame,
                           backdrop: SwiftUIVideoView(track, layoutMode: .fill).overlay(Blur()),
                           video: SwiftUIVideoView(track, layoutMode: .fill,
                                                   isRendering: Binding(get: { live.rendering },
                                                                        set: { live.rendering = $0 })))
                    .opacity(live.rendering ? 1 : 0)
                    .animation(Motion.enter(0.4), value: live.rendering)
                }
            }
        }
    }

    /// The blurred video filling the screen behind, and the sharp video at `frame` with soft top and bottom edges.
    private func framed(size: CGSize, frame: CGRect, backdrop: some View, video: some View) -> some View {
        ZStack(alignment: .topLeading) {
            backdrop
                .frame(width: size.width, height: size.height)
                .clipped()
            video
                .frame(width: frame.width, height: frame.height)
                .mask(
                    LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.08),
                                           .init(color: .black, location: 0.86), .init(color: .clear, location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                )
                .offset(x: frame.minX, y: frame.minY)
        }
    }

    /// Screen-wide square, placed so the chin (about 72% down the frame) clears the card even when it is lifted
    /// for smile and arms (card 220 pt tall, 300 pt above the bottom, +44 pt). The top bar sits over the hair.
    static func avatarFrame(in size: CGSize) -> CGRect {
        let side = size.width
        let liftedCardTop = size.height - (300 + 220 + 44)
        let top = min(max(liftedCardTop - 0.72 * side, -0.12 * side), size.height * 0.12)
        return CGRect(x: 0, y: top, width: side, height: side)
    }

    @ViewBuilder
    private var placeholder: some View {
        if let clip = Bundle.main.url(forResource: "avatar", withExtension: "mp4") {
            LoopingClip(url: clip)
        } else if let path = Bundle.main.path(forResource: "avatar", ofType: "jpg"),
                  let image = UIImage(contentsOfFile: path) {
            Image(uiImage: image)
                .resizable()
                .scaledToFill()
        } else {
            Stripes(a: Palette.stripeA, b: Palette.stripeB, period: 20)
        }
    }
}

/// A dark blur over whatever is behind it.
struct Blur: UIViewRepresentable {
    func makeUIView(context: Context) -> UIVisualEffectView {
        Self.make()
    }

    func updateUIView(_ uiView: UIVisualEffectView, context: Context) {}

    static func make() -> UIVisualEffectView {
        let view = UIVisualEffectView(effect: UIBlurEffect(style: .systemThickMaterialDark))
        view.contentView.backgroundColor = UIColor.black.withAlphaComponent(0.25)
        return view
    }
}

/// A muted, looping, aspect-filled local clip.
struct LoopingClip: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> PlayerView {
        let view = PlayerView()
        let player = AVQueuePlayer()
        player.isMuted = true
        view.looper = AVPlayerLooper(player: player, templateItem: AVPlayerItem(url: url))
        view.playerLayer.player = player
        view.playerLayer.videoGravity = .resizeAspectFill
        player.play()
        return view
    }

    func updateUIView(_ uiView: PlayerView, context: Context) {}

    static func dismantleUIView(_ uiView: PlayerView, coordinator: ()) {
        uiView.playerLayer.player?.pause()
    }

    class PlayerView: UIView {
        var looper: AVPlayerLooper?
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer {
            // swiftlint:disable:next force_cast
            layer as! AVPlayerLayer
        }
    }
}

/// AVPlayerLayer for the avatar clips: aspect-filled, optionally blurred, hidden until the first clip has a frame.
/// An AVPlayer can drive several layers, so the backdrop and the framed avatar share one player.
struct ClipLayer: UIViewRepresentable {
    let player: AVPlayer
    let blurred: Bool

    func makeUIView(context: Context) -> ClipView {
        let view = ClipView()
        view.backgroundColor = .clear
        view.playerLayer.backgroundColor = UIColor.clear.cgColor
        view.playerLayer.videoGravity = .resizeAspectFill
        view.playerLayer.player = player
        if blurred {
            let blur = Blur.make()
            blur.frame = view.bounds
            blur.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            view.addSubview(blur)
        }
        return view
    }

    func updateUIView(_ uiView: ClipView, context: Context) {}

    final class ClipView: LoopingClip.PlayerView {
        private var ready: NSKeyValueObservation?

        override init(frame: CGRect) {
            super.init(frame: frame)
            alpha = 0
            ready = playerLayer.observe(\.isReadyForDisplay, options: [.initial, .new]) { [weak self] layer, _ in
                let visible = layer.isReadyForDisplay
                DispatchQueue.main.async { self?.alpha = visible ? 1 : 0 }
            }
        }

        required init?(coder: NSCoder) {
            fatalError("init(coder:) is not used")
        }
    }
}
