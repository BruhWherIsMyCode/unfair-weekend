import AVFoundation
import Observation
import SwiftUI

/// The alert's clip area: an earlier check-in next to today's, same person, same task
/// (docs: Claude Design "HeartLine · Earlier check-in next to today", backend/proposals/baseline-video-comparison).
///
/// Today's clips are the attempt videos of the alert's call at the task with the most tries. The earlier clip is
/// picked on this phone: the newest earlier backend call in the history that was as usual or still learning,
/// with a finished, ready, not acted video of the same task. Nothing in the backend marks it as part of the
/// person's usual yet; that needs Agent 3's frozen-baseline selection.
@MainActor
@Observable
final class Comparison {
    enum Phase: Equatable {
        case idle, loading, ready
        /// The clip list could not be fetched: the error card with Retry.
        case failed
        /// The backend has no attempt videos for this call (recording off): the old single frame clip.
        case legacy
    }

    struct Source: Equatable {
        let url: URL
        let recorded: Date?
        let acted: Bool
    }

    private(set) var phase: Phase = .idle
    private(set) var task: CheckTask?
    private(set) var earlier: Source?
    /// One per try at today's task, oldest first. Empty: today's clip is not available.
    private(set) var today: [Source] = []
    var tryIndex = 0 {
        didSet { if tryIndex != oldValue { Task { await prepare(keepTime: true) } } }
    }
    let player = ComparisonPlayer()

    /// Which call this shows, so a repeated message does not reload it.
    @ObservationIgnored private(set) var callId: String?
    @ObservationIgnored private var loader: Task<Void, Never>?

    var todaySource: Source? { today.indices.contains(tryIndex) ? today[tryIndex] : nil }

    func clear() {
        loader?.cancel()
        player.setSources(left: nil, right: nil, keepTime: false)
        phase = .idle
        task = nil
        earlier = nil
        today = []
        callId = nil
    }

    // MARK: Loading

    func loadLive(callId: String, feed: FamilyFeed, history: CallHistory, before date: Date) {
        loader?.cancel()
        clear()
        self.callId = callId
        phase = .loading
        loader = Task { [weak self] in
            guard let self else { return }
            let clips: [AttemptClip]
            do {
                clips = try await feed.fetchCallClips(callId)
            } catch {
                if !Task.isCancelled { self.phase = .failed }
                return
            }
            guard !Task.isCancelled else { return }
            history.setAttempts(clips, callId: callId)
            guard let task = Self.alertTask(clips) else {
                self.phase = .legacy
                return
            }
            self.task = task
            var today: [Source] = []
            for clip in clips.filter({ $0.checkTask == task && $0.status != .failed }).sorted(by: { $0.recordedAt < $1.recordedAt }) {
                if let url = try? await feed.downloadVideo(clip) {
                    today.append(Source(url: url, recorded: clip.recorded, acted: clip.isActed))
                }
            }
            guard !Task.isCancelled else { return }
            self.today = today
            self.earlier = await Self.findEarlier(task: task, excluding: callId, before: date, feed: feed, history: history)
            guard !Task.isCancelled else { return }
            await self.prepare(keepTime: false)
            self.phase = .ready
        }
    }

    /// The staged scene: LocalAssets' clip.mp4 is today, and earlier.mp4, when present, the earlier check-in.
    func loadStaged(at date: Date) {
        clear()
        task = .smile
        today = LocalAssets.clipURL.map { [Source(url: $0, recorded: date, acted: false)] } ?? []
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: date).flatMap {
            Calendar.current.date(bySettingHour: 9, minute: 0, second: 0, of: $0)
        }
        earlier = LocalAssets.earlierClipURL.map { Source(url: $0, recorded: yesterday, acted: false) }
        phase = .loading
        Task {
            await prepare(keepTime: false)
            phase = .ready
        }
    }

    func retry(feed: FamilyFeed, history: CallHistory, before date: Date) {
        guard let callId else { return }
        loadLive(callId: callId, feed: feed, history: history, before: date)
    }

    private func prepare(keepTime: Bool) async {
        var left: (URL, Double)?
        var right: (URL, Double)?
        if let earlier { left = (earlier.url, await AttemptVideos.duration(earlier.url)) }
        if let today = todaySource { right = (today.url, await AttemptVideos.duration(today.url)) }
        player.setSources(left: left, right: right, keepTime: keepTime)
    }

    /// The task that showed the change. The message does not name it, but a change is checked again, so that task
    /// has the most tries. A tie (one try each, or no retry) goes to the smile.
    static func alertTask(_ clips: [AttemptClip]) -> CheckTask? {
        let tries = Dictionary(grouping: clips.compactMap(\.checkTask), by: { $0 }).mapValues(\.count)
        guard !tries.isEmpty else { return nil }
        return [CheckTask.smile, .arms].max { (tries[$0] ?? 0) < (tries[$1] ?? 0) }
    }

    private static func findEarlier(task: CheckTask, excluding callId: String, before date: Date,
                                    feed: FamilyFeed, history: CallHistory) async -> Source? {
        let candidates = history.records.filter {
            $0.live && $0.callId != nil && $0.callId != callId && $0.date < date
                && ($0.outcome == .asUsual || $0.outcome == .learning)
        }
        for record in candidates.prefix(10) {
            guard let id = record.callId else { continue }
            let clips = (try? await feed.fetchCallClips(id)) ?? record.attempts ?? []
            history.setAttempts(clips, callId: id)
            let usable = clips.filter {
                $0.checkTask == task && !$0.isActed && $0.captureStatus == "finished" && $0.isPlayable
            }
            for clip in usable.sorted(by: { $0.recordedAt > $1.recordedAt }) {
                if let url = try? await feed.downloadVideo(clip) {
                    return Source(url: url, recorded: clip.recorded, acted: false)
                }
            }
        }
        return nil
    }
}

/// One play button and one scrubber for both clips. Each clip starts at the start of its own task; a shorter one
/// holds its last frame while the other finishes. Muted, no autoplay, no loop.
@MainActor
@Observable
final class ComparisonPlayer {
    private(set) var left: AVPlayer?
    private(set) var right: AVPlayer?
    private(set) var leftDuration: Double = 0
    private(set) var rightDuration: Double = 0
    private(set) var time: Double = 0
    private(set) var playing = false

    @ObservationIgnored private var leftURL: URL?
    @ObservationIgnored private var rightURL: URL?
    @ObservationIgnored private var clock: Task<Void, Never>?

    var total: Double { max(left == nil ? 0 : leftDuration, right == nil ? 0 : rightDuration) }
    var canPlay: Bool { total > 0 }
    var atEnd: Bool { canPlay && !playing && time >= total - 0.01 }
    var leftEnded: Bool { left != nil && time > 0 && time >= leftDuration - 0.01 }
    var rightEnded: Bool { right != nil && time > 0 && time >= rightDuration - 0.01 }

    func setSources(left l: (URL, Double)?, right r: (URL, Double)?, keepTime: Bool) {
        pause()
        if l?.0 != leftURL { left = l.map { Self.player($0.0) }; leftURL = l?.0 }
        if r?.0 != rightURL { right = r.map { Self.player($0.0) }; rightURL = r?.0 }
        leftDuration = l?.1 ?? 0
        rightDuration = r?.1 ?? 0
        time = keepTime ? min(time, total) : 0
        seekAll(time)
    }

    func toggle() { playing ? pause() : play() }

    func play() {
        guard canPlay else { return }
        if time >= total - 0.01 { time = 0 }
        seekAll(time)
        let start = Date(), from = time
        if time < leftDuration - 0.01 { left?.play() }
        if time < rightDuration - 0.01 { right?.play() }
        playing = true
        clock?.cancel()
        clock = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(33))
                guard let self, !Task.isCancelled else { return }
                let t = from + Date().timeIntervalSince(start)
                if t >= self.total {
                    self.time = self.total
                    self.pause()
                    return
                }
                self.time = t
            }
        }
    }

    func pause() {
        clock?.cancel()
        left?.pause()
        right?.pause()
        playing = false
    }

    /// Scrubbing pauses both.
    func scrub(to t: Double) {
        if playing { pause() }
        time = max(0, min(total, t))
        seekAll(time)
    }

    private func seekAll(_ t: Double) {
        seek(left, to: min(t, max(0, leftDuration - 0.02)))
        seek(right, to: min(t, max(0, rightDuration - 0.02)))
    }

    private func seek(_ player: AVPlayer?, to t: Double) {
        player?.seek(to: CMTime(seconds: t, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
    }

    private static func player(_ url: URL) -> AVPlayer {
        let p = AVPlayer(url: url)
        p.isMuted = true
        p.actionAtItemEnd = .pause
        p.automaticallyWaitsToMinimizeStalling = false
        return p
    }
}

// MARK: Views

/// The clip area of the alert: two equal panels, then the control row.
struct ComparisonView: View {
    @Bindable var comparison: Comparison
    let height: CGFloat
    var retry: () -> Void

    private var player: ComparisonPlayer { comparison.player }

    var body: some View {
        VStack(spacing: 0) {
            if comparison.phase == .failed {
                errorCard
            } else {
                HStack(spacing: 8) {
                    earlierPanel
                    todayPanel
                }
                .frame(maxHeight: .infinity)
                controls
                    .frame(height: 44)
                    .padding(.top, 8)
            }
        }
        .frame(height: height)
        .onDisappear { player.pause() }
    }

    private var loading: Bool { comparison.phase == .loading || comparison.phase == .idle }
    private var taskName: String { comparison.task?.spoken ?? String(localized: "check") }

    private var earlierPanel: some View {
        Group {
            if loading {
                ComparisonPanel(content: .loading, a11y: String(localized: "Earlier check-in, loading"))
            } else if let source = comparison.earlier, let player = player.left {
                ComparisonPanel(content: .clip(player), date: Self.date(source.recorded), ended: self.player.leftEnded,
                                a11y: Self.spoken(source.recorded, task: taskName))
            } else {
                ComparisonPanel(content: .message(String(localized: "No earlier clip saved"), nil),
                                a11y: String(localized: "No earlier clip saved"))
            }
        }
        .accessibilityIdentifier("comparison.earlier")
    }

    private var todayPanel: some View {
        Group {
            if loading {
                ComparisonPanel(content: .loading, a11y: String(localized: "Today, loading"))
            } else if let source = comparison.todaySource, let player = player.right {
                let tries = comparison.today.count > 1 ? String(localized: ", try \(comparison.tryIndex + 1)") : ""
                ComparisonPanel(content: .clip(player), date: Self.date(source.recorded, nowPrefix: true),
                                ended: self.player.rightEnded, acted: source.acted,
                                a11y: (source.acted ? String(localized: "Acted, demo recording. ") : "")
                                    + Self.spoken(source.recorded, task: taskName, nowPrefix: true) + tries)
            } else {
                ComparisonPanel(content: .message(String(localized: "Today's clip isn't available"), nil),
                                a11y: String(localized: "Today's clip isn't available"))
            }
        }
        .accessibilityIdentifier("comparison.today")
    }

    private var controls: some View {
        HStack(spacing: 10) {
            Button(action: player.toggle) {
                Image(systemName: player.playing ? "pause.fill" : player.atEnd ? "arrow.counterclockwise" : "play.fill")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Palette.ink)
                    .frame(width: 44, height: 44)
                    .background(Palette.grey, in: Circle())
            }
            .buttonStyle(PressStyle())
            .disabled(!player.canPlay)
            .accessibilityLabel(playLabel)
            .accessibilityIdentifier("comparison.play")

            Slider(value: Binding(get: { player.time }, set: { player.scrub(to: $0) }),
                   in: 0...max(player.total, 0.1))
                .tint(Palette.back)
                .disabled(!player.canPlay)
                .accessibilityLabel(Text("Position in both clips"))
                .accessibilityAdjustableAction { direction in
                    player.scrub(to: player.time + (direction == .increment ? 0.5 : -0.5))
                }

            if comparison.today.count > 1 {
                Picker("Today's try", selection: $comparison.tryIndex) {
                    ForEach(comparison.today.indices, id: \.self) { i in
                        Text("Try \(i + 1)").tag(i)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 132)
            }
        }
        .opacity(player.canPlay ? 1 : 0.4)
    }

    private var playLabel: Text {
        let both = player.left != nil && player.right != nil
        if player.playing { return Text(both ? "Pause both clips" : "Pause clip") }
        if player.atEnd { return Text(both ? "Replay both clips from the start" : "Replay clip") }
        return Text(both ? "Play both clips" : "Play clip")
    }

    private var errorCard: some View {
        VStack(spacing: 14) {
            Text("Couldn't load the clips")
                .font(.system(size: 17, weight: .semibold))
                .tracking(-0.4)
            Button(action: retry) {
                Text("Retry")
                    .font(.system(size: 17, weight: .semibold))
                    .tracking(-0.4)
                    .foregroundStyle(Palette.ink)
                    .padding(.horizontal, 22)
                    .frame(height: 44)
                    .background(Palette.grey, in: Capsule())
            }
            .buttonStyle(PressStyle())
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
        .accessibilityElement(children: .contain)
    }

    /// "Fri 13 Nov · 09:00"; "Now, Sat 14 Nov · 13:01" for a clip under 5 minutes old.
    static func date(_ d: Date?, nowPrefix: Bool = false) -> String? {
        guard let d else { return nil }
        let text = Clock.shortDay(d) + " · " + Clock.time(d)
        return nowPrefix && Date().timeIntervalSince(d) < 300 ? String(localized: "Now, \(text)") : text
    }

    static func spoken(_ d: Date?, task: String, nowPrefix: Bool = false) -> String {
        guard let d else { return task }
        let text = Clock.date(d) + ", " + Clock.time(d) + ", " + task
        return nowPrefix && Date().timeIntervalSince(d) < 300 ? String(localized: "Now, \(text)") : text
    }
}

/// One side: the clip on the letterbox colour with its date strip under it, or a quiet message panel.
struct ComparisonPanel: View {
    enum Content {
        case clip(AVPlayer)
        case loading
        case message(String, String?)
    }

    let content: Content
    var date: String?
    var ended = false
    var acted = false
    let a11y: String

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                switch content {
                case .clip(let player):
                    Palette.letterbox
                    VideoLayer(player: player)
                case .loading:
                    Palette.grey
                    Text("Loading…")
                        .font(.system(size: 13))
                        .foregroundStyle(Palette.secondary)
                case .message(let title, let sub):
                    Palette.grey
                    VStack(spacing: 5) {
                        Text(verbatim: title)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                        if let sub {
                            Text(verbatim: sub)
                                .font(.system(size: 12))
                                .foregroundStyle(Palette.secondary)
                        }
                    }
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 12)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .overlay(alignment: .topTrailing) {
                if ended {
                    Text("Ended")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background(Palette.ink.opacity(0.72), in: Capsule())
                        .padding(.top, acted ? 34 : 6)
                        .padding(.trailing, 6)
                }
            }
            .overlay(alignment: .top) {
                if acted {
                    Text("ACTED, demo recording")
                        .font(.system(size: 12, weight: .bold))
                        .tracking(0.4)
                        .foregroundStyle(.white)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .frame(maxWidth: .infinity)
                        .frame(height: 28)
                        .background(Palette.ink)
                }
            }
            if let date {
                Text(verbatim: date)
                    .font(.system(size: 13, weight: .semibold).monospacedDigit())
                    .tracking(-0.1)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .frame(maxWidth: .infinity)
                    .frame(height: 24)
                    .padding(.horizontal, 10)
            }
        }
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay {
            if acted {
                RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Palette.ink, lineWidth: 2)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: a11y))
    }
}

/// An AVPlayer drawn aspect-fit: never cropped, stretched or mirrored.
struct VideoLayer: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerUIView {
        let v = PlayerUIView()
        v.playerLayer.videoGravity = .resizeAspect
        v.playerLayer.player = player
        return v
    }

    func updateUIView(_ uiView: PlayerUIView, context: Context) {
        if uiView.playerLayer.player !== player { uiView.playerLayer.player = player }
    }

    final class PlayerUIView: UIView {
        override static var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}
