import SwiftUI

/// Hidden debug screen (Settings > Camera debug): the camera, the task's timeline and what the analysis sees.
/// Not part of the designed call; plain on purpose.
struct CameraDebugView: View {
    @State var model: CameraDebugModel
    let onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top, spacing: 10) {
                        CameraPreview(camera: model.cameraForPreview)
                            .background(Color.black)
                            .frame(maxWidth: .infinity)
                            .aspectRatio(3 / 4, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .overlay(alignment: .bottomLeading) { tag("Camera") }
                        AnalysedFrame(image: model.frameImage, frame: model.frame)
                            .frame(maxWidth: .infinity)
                            .aspectRatio(3 / 4, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .overlay(alignment: .bottomLeading) { tag("What the analysis saw") }
                    }
                    timeline
                    facts
                    observations
                    result
                }
                .padding(16)
            }
            controls
        }
        .background(Color.black.ignoresSafeArea())
        .foregroundStyle(.white)
        .preferredColorScheme(.dark)
        .onAppear { model.appear() }
        .onDisappear { model.disappear() }
    }

    private var header: some View {
        HStack {
            Text("Camera debug").font(.headline)
            Spacer()
            Button("Close", action: onClose).disabled(model.isRunning)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func tag(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(Color.black.opacity(0.6), in: Capsule())
            .padding(6)
    }

    // MARK: timeline

    @ViewBuilder
    private var timeline: some View {
        switch model.state {
        case .ready:
            Text(model.task == .smile
                 ? "Face the camera. Relax, then smile when the screen says so (6 s)."
                 : "Phone in your right hand. Raise your left hand beside your face; swap when told (15 s).")
                .font(.callout).foregroundStyle(.secondary)
        case .starting:
            Text("Starting…").font(.title3.bold())
        case .running(let started, let durationMs):
            TimelineView(.periodic(from: started, by: 0.1)) { context in
                let elapsed = Int(context.date.timeIntervalSince(started) * 1000)
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.instruction(atMs: elapsed)).font(.title2.bold())
                    Text(String(format: "%.1f s of %.0f s · sent %d · analysed %d",
                                Double(min(elapsed, durationMs)) / 1000, Double(durationMs) / 1000,
                                model.framesSent, model.framesAnalysed))
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
        case .finishing:
            Text("Waiting for the analysis…").font(.title3.bold())
        case .done:
            Text("Done").font(.title3.bold())
        case .failed(let message):
            Text(message).font(.callout).foregroundStyle(.orange)
        }
    }

    // MARK: live facts

    @ViewBuilder
    private var facts: some View {
        if let frame = model.frame {
            section("Latest frame (\(frame.ms) ms)") {
                if let w = frame.width, let h = frame.height {
                    row("Frame", "\(Int(w)) × \(Int(h))" + (w > h ? " landscape: turned sideways!" : " portrait"),
                        ok: h >= w)
                }
                row("Face", frame.face ? (frame.faceSmall == true ? "found, too small: come closer" : "found")
                                       : "not found", ok: frame.face && frame.faceSmall != true)
                if frame.task == "arms" {
                    let expected = frame.expected.map { "her \($0.uppercased())" } ?? "?"
                    row("Wanted", "hand on \(expected) side of her face", ok: nil)
                    let hands = frame.hands ?? []
                    if hands.isEmpty {
                        row("Hands", "none seen", ok: false)
                    }
                    ForEach(Array(hands.enumerated()), id: \.offset) { _, hand in
                        let onWanted = frame.expected.map { "her_\($0)" == hand.side } ?? false
                        row("Hand", "\(Self.sideText(hand.side)), \(String(format: "%.1f", hand.eyeWidths ?? 0)) eye widths"
                            + " from the midline · if the mirroring were flipped: \(Self.sideText(hand.ifMirroredFlipped))",
                            ok: onWanted)
                    }
                    row("Mirrored", frame.mirrored ? "on" : "off", ok: nil)
                } else if let reading = frame.reading {
                    row("Readable", reading["usable"]?.bool == true ? "yes"
                                    : "no: \(reading["reason"]?.string ?? "?")", ok: reading["usable"]?.bool == true)
                    row("Smile strength", Self.number(reading["smile_strength"]), ok: nil)
                    row("Corner height", Self.number(reading["corner_height"]), ok: nil)
                    row("Head yaw / pitch / roll", ["yaw_deg", "pitch_deg", "roll_deg"]
                        .map { Self.number(reading[$0], digits: 0) + "°" }.joined(separator: " / "), ok: nil)
                }
            }
        }
    }

    @ViewBuilder
    private var observations: some View {
        if !model.observations.isEmpty {
            section("Analysis events (newest first)") {
                ForEach(Array(model.observations.suffix(12).reversed().enumerated()), id: \.offset) { _, obs in
                    let quality = obs["quality"]
                    let status = quality?["status"]?.string ?? "?"
                    let reasons = quality?["reasons"]?.strings ?? []
                    let signal = obs["signal"]?.string.flatMap { $0 == "none" ? nil : $0 }
                    Text("\(Int(obs["elapsed_ms"]?.number ?? 0)) ms · \(obs["phase"]?.string ?? "") · \(status)"
                         + (reasons.isEmpty ? "" : ": " + reasons.joined(separator: ", "))
                         + (signal.map { " · signal \($0)" } ?? ""))
                        .font(.caption.monospaced())
                        .foregroundStyle(status == "usable" ? Color.green : status == "pending" ? .secondary : .orange)
                }
            }
        }
    }

    @ViewBuilder
    private var result: some View {
        if let result = model.result {
            section("Result") {
                row("Outcome", result["outcome"]?.string ?? "?", ok: result["outcome"]?.string == "completed")
                let quality = result["quality"]
                row("Quality", (quality?["status"]?.string ?? "?")
                    + ((quality?["reasons"]?.strings).map { $0.isEmpty ? "" : ": " + $0.joined(separator: ", ") } ?? ""),
                    ok: quality?["status"]?.string == "usable")
                row("Performance", result["performance"]?.string ?? "?", ok: nil)
                if let reading = result["reading"], reading != .null {
                    let range = [reading["usual_low"], reading["usual_high"]].map { Self.number($0) }.joined(separator: " – ")
                    row("Reading", "\(Self.number(reading["value"])) (\(reading["metric_id"]?.string ?? "")),"
                        + " usual \(range)", ok: reading["outside"]?.bool.map { !$0 })
                } else {
                    row("Reading", "none", ok: nil)
                }
                if let interruption = result["interruption"], interruption != .null {
                    row("Interrupted", interruption["reason"]?.string ?? "yes", ok: false)
                }
                Text("Debug only: nothing was stored and her usual was not changed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: controls

    private var controls: some View {
        VStack(spacing: 10) {
            Picker("Task", selection: $model.task) {
                ForEach(CameraDebugModel.Task.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            Picker("Mirroring", selection: $model.mirroring) {
                ForEach(CameraDebugModel.Mirroring.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            Button(action: model.start) {
                Text(model.isRunning ? "Running…" : "Start \(model.task.title.lowercased()) test")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .background(model.isRunning ? Color.gray : Color.green, in: RoundedRectangle(cornerRadius: 14))
            }
            .disabled(model.isRunning)
        }
        .disabled(model.isRunning)
        .padding(16)
        .background(Color(white: 0.08))
    }

    // MARK: helpers

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline.bold())
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(white: 0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    private func row(_ label: String, _ value: String, ok: Bool?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(ok == nil ? Color.gray : ok == true ? Color.green : Color.orange)
                .frame(width: 8, height: 8)
            Text(label).font(.caption.bold()).frame(width: 96, alignment: .leading)
            Text(value).font(.caption)
        }
    }

    static func sideText(_ side: String?) -> String {
        switch side {
        case "her_left": return "her LEFT side"
        case "her_right": return "her RIGHT side"
        default: return "?"
        }
    }

    static func number(_ value: JSONValue?, digits: Int = 3) -> String {
        guard let n = value?.number else { return "–" }
        return String(format: "%.\(digits)f", n)
    }
}

/// The frame the analysis read, with what it found drawn on it: the eye line and face midline (blue), mouth corners
/// (blue), each hand's box and palm centre (green on the wanted side, orange on the other). When the analysis
/// flips frames (mirrored), the picture is flipped too, so it matches the analysis's own left and right.
struct AnalysedFrame: View {
    let image: UIImage?
    let frame: DebugFrame?

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Color(white: 0.1)
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
                        .scaleEffect(x: frame?.mirrored == true ? -1 : 1, y: 1)
                } else {
                    Text("No analysed frame yet").font(.caption).foregroundStyle(.secondary)
                }
                if let frame, image != nil {
                    overlay(frame, in: geo.size)
                }
            }
        }
    }

    private func overlay(_ frame: DebugFrame, in size: CGSize) -> some View {
        let width = frame.width ?? 480, height = frame.height ?? 640
        let scale = max(size.width / width, size.height / height)  // matches scaledToFill
        let dx = (size.width - width * scale) / 2, dy = (size.height - height * scale) / 2
        func point(_ p: [Double]) -> CGPoint { CGPoint(x: p[0] * scale + dx, y: p[1] * scale + dy) }
        let wanted = frame.expected.map { "her_\($0)" }
        return Canvas { context, _ in
            let eyes = (frame.eyePoints ?? []).filter { $0.count >= 2 }.map(point)
            if eyes.count == 2 {
                var line = Path()
                line.move(to: eyes[0])
                line.addLine(to: eyes[1])
                context.stroke(line, with: .color(.cyan), lineWidth: 2)
            }
            if let mid = frame.midlineX {
                var midline = Path()
                let x = mid * scale + dx
                midline.move(to: CGPoint(x: x, y: 0))
                midline.addLine(to: CGPoint(x: x, y: size.height))
                context.stroke(midline, with: .color(.cyan.opacity(0.7)), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
            }
            for p in (frame.mouthPoints ?? []).filter({ $0.count >= 2 }).map(point) {
                context.fill(Path(ellipseIn: CGRect(x: p.x - 3, y: p.y - 3, width: 6, height: 6)), with: .color(.cyan))
            }
            for hand in frame.hands ?? [] {
                let color: Color = hand.side == wanted ? .green : .orange
                if let box = hand.box, box.count == 4 {
                    let a = point([box[0], box[1]]), b = point([box[2], box[3]])
                    context.stroke(Path(CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x),
                                               height: abs(b.y - a.y))), with: .color(color), lineWidth: 2)
                }
                if let c = hand.centre, c.count >= 2 {
                    let p = point(c)
                    context.fill(Path(ellipseIn: CGRect(x: p.x - 5, y: p.y - 5, width: 10, height: 10)), with: .color(color))
                }
            }
        }
        // Not flipped: the points are already in the analysis's (flipped, when mirrored) picture.
    }
}
