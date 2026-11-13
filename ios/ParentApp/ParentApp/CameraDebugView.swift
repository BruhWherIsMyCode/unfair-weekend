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
                    family
                    facts
                    tally
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
        .onChange(of: model.task) { model.loadVerdict() }
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

    // MARK: what the family would get

    /// The check tries of this task judged with the call's own rules: would the family be told, and why.
    @ViewBuilder
    private var family: some View {
        if let verdict = model.verdict {
            section("What the family would get") {
                Text(verdict.headline)
                    .font(.callout.bold())
                    .foregroundStyle(Self.verdictColor(verdict))
                ForEach(Array(verdict.lines.enumerated()), id: \.offset) { _, line in
                    Text(line).font(.caption).foregroundStyle(.secondary)
                }
                if let message = verdict.familyMessage {
                    Text("“\(message)”")
                        .font(.caption.italic())
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(white: 0.18), in: RoundedRectangle(cornerRadius: 8))
                }
                ForEach(verdict.tries, id: \.number) { attempt in
                    row("Try \(attempt.number)", Self.tryText(attempt), ok: Self.tryOK(attempt))
                }
                row("Usual", Self.usualText(verdict), ok: verdict.usual.ready)
                Button("Start over", action: model.startOver)
                    .font(.caption.bold())
                    .disabled(model.isRunning || (verdict.tries.isEmpty && verdict.practiceTries == 0))
            }
        }
    }

    static func verdictColor(_ verdict: DebugVerdict) -> Color {
        switch verdict.status {
        case "change_noticed": return .red
        case "incomplete": return .orange
        case "as_usual": return .green
        default: return verdict.state == "try_again" ? .yellow : .white
        }
    }

    static func tryText(_ attempt: DebugVerdict.Try) -> String {
        guard attempt.measured else {
            return "not measured: " + (attempt.reasons.isEmpty ? "?" : attempt.reasons.joined(separator: ", "))
                .replacingOccurrences(of: "_", with: " ")
        }
        var text = attempt.value.map { String(format: "%.3f", $0) } ?? "–"
        if let hands = attempt.hands {
            text = String(format: "left %.3f · right %.3f", hands["left"] ?? .nan, hands["right"] ?? .nan)
        }
        guard attempt.compared else { return text + " · not compared (no usual)" }
        let high = attempt.usualHigh.map { String(format: "%.3f", $0) } ?? "–"
        if attempt.outside == true {
            return text + " · OUTSIDE her usual (up to \(high))" + (attempt.side.map { ", her \($0) side" } ?? "")
        }
        return text + " · inside her usual (up to \(high))"
    }

    static func tryOK(_ attempt: DebugVerdict.Try) -> Bool? {
        guard attempt.measured else { return false }
        guard attempt.compared else { return nil }
        return attempt.outside != true
    }

    static func usualText(_ verdict: DebugVerdict) -> String {
        let usual = verdict.usual
        let stored = "stored \(usual.storedSessions ?? 0) of \(usual.storedNeeded ?? 0) calls"
        let practice = "practice \(verdict.practiceTries) of \(verdict.practiceNeeded) teach tries"
        let ranges = (usual.ranges ?? [:]).sorted { $0.key < $1.key }.compactMap { key, range -> String? in
            guard range.count == 2, let low = range[0], let high = range[1] else { return nil }
            return String(format: "%@ %.3f–%.3f", key == "value" ? "range" : key, low, high)
        }.joined(separator: " · ")
        switch usual.source {
        case "stored": return "her stored usual · \(ranges)"
        case "practice": return "practice usual · \(ranges) · \(stored)"
        default: return "none yet: \(stored), \(practice)"
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
                if let eyes = frame.eyeDistancePx {
                    row("Face size", String(format: "%.0f px between the eyes (the analysis needs 40)", eyes),
                        ok: eyes >= 40)
                }
                if let roll = frame.rollDeg {
                    row("Head tilt", String(format: "%.0f°", roll), ok: abs(roll) < 20)
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

    /// Where the hand was in each hold: the left/right question in numbers, for the whole run.
    @ViewBuilder
    private var tally: some View {
        let phases = [("hold_left", "left"), ("hold_right", "right")].filter { model.tally[$0.0] != nil }
        if !phases.isEmpty || model.cameraStats != nil {
            section("This run") {
                ForEach(phases, id: \.0) { phase, wanted in
                    let counts = model.tally[phase] ?? [:]
                    let total = max(1, counts.values.reduce(0, +))
                    let right = counts[wanted] ?? 0
                    let wrong = counts[wanted == "left" ? "right" : "left"] ?? 0
                    row(wanted == "left" ? "Left hold" : "Right hold",
                        "her \(wanted.uppercased()) \(right) · other side \(wrong) · no hand \(counts["none"] ?? 0)"
                        + " · no face \(counts["no_face"] ?? 0)",
                        ok: right * 2 > total && wrong == 0)
                }
                if let camera = model.cameraStats {
                    row("Camera", "raw \(camera.rawWidth) × \(camera.rawHeight), mirrored \(camera.mirrored ? "on" : "off"),"
                        + " angle \(Int(camera.angle))° · turned \(camera.fixed) · as is \(camera.asIs)",
                        ok: camera.asIs == 0)
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
            Picker("Try", selection: $model.purpose) {
                ForEach(CameraDebugModel.Purpose.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            Button(action: model.start) {
                Text(model.isRunning ? "Running…"
                     : model.purpose == .teach ? "Teach usual: normal \(model.task.title.lowercased())"
                                               : "Start \(model.task.title.lowercased()) test")
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
