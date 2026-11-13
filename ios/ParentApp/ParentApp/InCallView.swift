import AVFoundation
import ParentCallKit
import SwiftUI

/// Design screens 3 to 8: the live call. Everything here is drawn from `screen`; nothing is timed.
struct InCallView: View {
    let screen: CallScreen
    let title: String
    let camera: SelfViewCamera
    let clips: AVPlayer?
    let onEnd: () -> Void

    /// Avatar settle 1.08 -> 1.0 and the late fade-in of self-view and End.
    @State private var settled = false
    @State private var controlsShown = false

    var body: some View {
        ZStack {
            AvatarBackground(clips: clips)
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
        case "arms": return "Raise both arms"
        default: return "Follow the voice"
        }
    }

    static func instruction(_ task: String) -> String {
        switch task {
        case "smile": return "Look at the screen"
        case "arms": return "Keep them up"
        default: return "Look at the screen"
        }
    }

    /// Neutral framing hints from the backend. Unknown codes show nothing.
    static func hintText(_ hint: String?) -> String? {
        switch hint {
        case "look_at_camera": return "Look at the screen"
        case "show_both_arms": return "Show both arms"
        case "check_camera": return "Check your camera"
        default: return nil
        }
    }
}

/// The full-screen avatar: the backend's prepared clip for the current line (Simli video with its voice),
/// holding its last frame between lines. Before the first clip, or when the backend offers none, a local
/// loop or still from ios/ParentApp/LocalAssets (git-ignored), else the design's striped placeholder.
struct AvatarBackground: View {
    let clips: AVPlayer?

    var body: some View {
        ZStack {
            placeholder
            if let clips {
                GeometryReader { geometry in
                    let side = Self.clipSide(for: geometry.size)
                    ClipLayer(player: clips)
                        .frame(width: side, height: side)
                        .position(x: geometry.size.width / 2, y: geometry.size.height - side / 2)
                }
            }
        }
    }

    /// Simli clips are square, with the mouth about 52% down the frame. Filled to the screen height, the mouth
    /// would sit behind the card (220 pt tall, 300 pt above the bottom, lifted 44 pt during smile and arms).
    /// Bottom-aligned at this size, the mouth stays just above even the lifted card on any phone height; the top
    /// of the hair is cropped instead.
    static func clipSide(for size: CGSize) -> CGFloat {
        let mouthToBottom: CGFloat = 300 + 220 + 44 + 10
        return max((mouthToBottom / (1 - 0.52)).rounded(), size.width, size.height)
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

    final class PlayerView: UIView {
        var looper: AVPlayerLooper?
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer {
            // swiftlint:disable:next force_cast
            layer as! AVPlayerLayer
        }
    }
}

/// AVPlayerLayer for the avatar clips: aspect-filled, transparent until the first clip plays.
struct ClipLayer: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> LoopingClip.PlayerView {
        let view = LoopingClip.PlayerView()
        view.backgroundColor = .clear
        view.playerLayer.backgroundColor = UIColor.clear.cgColor
        view.playerLayer.videoGravity = .resizeAspectFill
        view.playerLayer.player = player
        return view
    }

    func updateUIView(_ uiView: LoopingClip.PlayerView, context: Context) {}
}
