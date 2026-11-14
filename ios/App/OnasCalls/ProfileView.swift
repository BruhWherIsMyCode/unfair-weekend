import AVKit
import PhotosUI
import SwiftUI

/// The person's profile: who they are, the alert waiting (if any), and every check-in call. Opened from their
/// row on Family. Edit changes the profile; a call opens its report, the family's note and the clip to keep.
struct ProfileView: View {
    @Environment(DemoStore.self) private var store
    @State private var editing = false
    @State private var editMode: EditMode = .inactive
    @State private var path: [String] = []

    var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                backBar
                List {
                    header
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                    if store.phase != .asUsual {
                        alertBanner
                            .listRowInsets(EdgeInsets())
                            .listRowBackground(Color.clear)
                    }
                    about
                    calls
                }
                .listStyle(.insetGrouped)
                .scrollContentBackground(.hidden)
                .environment(\.editMode, $editMode)
            }
            .background(Palette.page)
            .foregroundStyle(Palette.ink)
            .toolbar(.hidden, for: .navigationBar)
            .navigationTitle(store.name)
            .navigationDestination(for: String.self) { id in
                CallDetailView(recordId: id)
            }
        }
        .tint(Palette.back)
        .sheet(isPresented: $editing) { EditProfileView() }
        .task { await store.refreshAttempts(store.history.records) }
    }

    private var backBar: some View {
        HStack {
            Button(action: store.backToFamily) {
                HStack(spacing: 4) {
                    Image(systemName: "chevron.left").font(.system(size: 20, weight: .semibold))
                    Text("Family").font(.system(size: 17)).tracking(-0.4)
                }
                .foregroundStyle(Palette.back)
                .frame(height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressStyle())
            Spacer()
            Button("Edit") { editing = true }
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Palette.back)
                .frame(height: 44)
                .accessibilityIdentifier("profile.edit")
        }
        .padding(.horizontal, 16)
    }

    private var header: some View {
        VStack(spacing: 10) {
            Avatar(size: 112, label: store.name, photo: store.photo, fontSize: 13)
            Text(verbatim: store.name)
                .font(.system(size: 30, weight: .bold))
                .tracking(-0.7)
            let line = [store.profile.relation.trimmingCharacters(in: .whitespaces), store.fedyaDigits]
                .filter { !$0.isEmpty }
                .joined(separator: " · ")
            if !line.isEmpty {
                Text(verbatim: line)
                    .font(.system(size: 17))
                    .foregroundStyle(Palette.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    /// The alert on screen right now: back to its report and choices.
    private var alertBanner: some View {
        Button(action: store.openAlert) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    NotUsualLabel()
                    Text(store.phase == .declined ? "Call \(store.name) again" : "Open today's report")
                        .font(.system(size: 20, weight: .bold))
                        .tracking(-0.4)
                        .foregroundStyle(Palette.amberInk)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Palette.amberLabel)
            }
            .padding(18)
            .background(Palette.amberCard, in: RoundedRectangle(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(Palette.amberOutline, lineWidth: 1.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .padding(.horizontal, 20)
        .padding(.bottom, 8)
    }

    private var calls: some View {
        Section {
            if store.history.records.isEmpty {
                Text("No calls yet.")
                    .foregroundStyle(Palette.secondary)
            }
            ForEach(store.history.records) { record in
                NavigationLink(value: record.id) {
                    CallRow(record: record)
                }
                .listRowBackground(Color.white)
            }
            .onDelete { offsets in
                store.history.delete(offsets.map { store.history.records[$0].id })
            }
        } header: {
            HStack {
                Text("Calls")
                Spacer()
                if !store.history.records.isEmpty {
                    Button(editMode.isEditing ? "Done" : "Edit history") {
                        withAnimation { editMode = editMode.isEditing ? .inactive : .active }
                    }
                    .textCase(nil)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Palette.back)
                }
            }
        }
    }

    @ViewBuilder
    private var about: some View {
        let notes = store.profile.notes.trimmingCharacters(in: .whitespacesAndNewlines)
        if !notes.isEmpty {
            Section("Notes") {
                Text(verbatim: notes)
                    .font(.system(size: 17))
                    .listRowBackground(Color.white)
            }
        }
    }
}

/// One call in the list: the clip thumbnail, time, outcome pill, the first lines of the report and the clip's state.
private struct CallRow: View {
    @Environment(DemoStore.self) private var store
    let record: CallRecord

    var body: some View {
        let badge = ClipBadge(record)
        HStack(alignment: .top, spacing: 14) {
            if let badge {
                ClipThumb(kind: badge.kind, posterClipId: badge.posterClipId)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(verbatim: CallRow.when(record.date))
                        .font(.system(size: 17, weight: .semibold))
                        .monospacedDigit()
                    Spacer(minLength: 0)
                    OutcomePill(outcome: record.outcome)
                }
                Text(verbatim: record.text)
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.secondary)
                    .lineLimit(2)
                if let badge {
                    Text(verbatim: badge.line)
                        .font(.system(size: 13))
                        .foregroundStyle(Palette.faint)
                }
                if let follow = CallRow.followUp(record, pronoun: store.pronoun) {
                    Text(verbatim: follow)
                        .font(.system(size: 13))
                        .foregroundStyle(Palette.faint)
                }
            }
        }
        .padding(.vertical, 4)
    }

    /// "Today 09:02", "Yesterday 09:04", "Wed 11 Nov 09:11".
    static func when(_ date: Date) -> String {
        let cal = Calendar.current
        let time = Clock.time(date)
        if cal.isDateInToday(date) { return String(localized: "Today \(time)") }
        if cal.isDateInYesterday(date) { return String(localized: "Yesterday \(time)") }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.setLocalizedDateFormatFromTemplate("EEEdMMM")
        return f.string(from: date).replacingOccurrences(of: ",", with: "") + " " + time
    }

    /// What the family did about it, and their note.
    static func followUp(_ record: CallRecord, pronoun: Pronoun) -> String? {
        var parts: [String] = []
        if let t = record.calledAt { parts.append(String(localized: "You called at \(Clock.time(t))")) }
        if let t = record.markedFineAt { parts.append(pronoun.saidFine + " " + Clock.time(t)) }
        let note = record.note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !note.isEmpty { parts.append(String(localized: "Note: \(note)")) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// What a call's clip looks like in the history: one of four states, and the line under the report.
struct ClipBadge {
    enum Kind { case ready, preparing, failed, expired }
    let kind: Kind
    let line: String
    /// An attempt video on this phone, for the thumbnail's first frame.
    let posterClipId: String?

    init?(_ record: CallRecord) {
        let attempts = record.attempts ?? []
        let playable = attempts.filter(\.isPlayable)
        posterClipId = playable.first(where: \.isOnPhone)?.clipId
        if !playable.isEmpty {
            kind = .ready
            line = Self.kept(playable)
        } else if record.clip != nil {
            kind = .ready
            line = String(localized: "Kept on this phone")
        } else if attempts.isEmpty {
            return nil
        } else if attempts.contains(where: { $0.status == .preparing }) {
            kind = .preparing
            line = String(localized: "Preparing clip…")
        } else if attempts.contains(where: { $0.status == .expired }) {
            kind = .expired
            line = String(localized: "Clip expired")
        } else {
            kind = .failed
            line = String(localized: "Clip couldn't be saved")
        }
    }

    /// One attempt's line in the call detail.
    static func line(_ clip: AttemptClip) -> String {
        let state: String
        if clip.isPlayable {
            state = kept([clip])
        } else {
            switch clip.status {
            case .preparing: state = String(localized: "Preparing clip…")
            case .expired: state = String(localized: "Clip expired")
            case .failed, .ready: state = String(localized: "Clip couldn't be saved")
            }
        }
        return clip.isActed ? String(localized: "Acted · \(state)") : state
    }

    /// "Kept until Sat 21 Nov" while the backend has it, then "Kept on this phone" if it was downloaded.
    private static func kept(_ clips: [AttemptClip]) -> String {
        if let until = clips.filter({ $0.status == .ready }).compactMap(\.expires).max() {
            return String(localized: "Kept until \(Clock.shortDay(until))")
        }
        return String(localized: "Kept on this phone")
    }

    var a11y: String {
        switch kind {
        case .ready: String(localized: "Clip")
        case .preparing: String(localized: "Preparing clip")
        case .failed: String(localized: "Clip couldn't be saved")
        case .expired: String(localized: "Clip expired")
        }
    }
}

/// 48 x 60 clip thumbnail: first frame with a play mark, or the state's mark on the neutral fill.
struct ClipThumb: View {
    let kind: ClipBadge.Kind
    let posterClipId: String?
    @State private var poster: UIImage?

    var body: some View {
        ZStack {
            Palette.grey
            switch kind {
            case .ready:
                if let poster {
                    Image(uiImage: poster).resizable().scaledToFill()
                } else {
                    Stripes(colors: Palette.clipStripes, band: 5)
                }
                Image(systemName: "play.fill")
                    .font(.system(size: 10))
                    .foregroundStyle(.white)
                    .frame(width: 26, height: 26)
                    .background(Palette.ink.opacity(0.55), in: Circle())
            case .preparing:
                Image(systemName: "ellipsis").font(.system(size: 17, weight: .bold)).foregroundStyle(Palette.faint)
            case .failed:
                Image(systemName: "exclamationmark.circle").font(.system(size: 20)).foregroundStyle(Palette.faint)
            case .expired:
                Image(systemName: "clock").font(.system(size: 20)).foregroundStyle(Palette.faint)
            }
        }
        .frame(width: 48, height: 60)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .accessibilityHidden(true)
        .task(id: posterClipId) {
            guard let id = posterClipId, let url = AttemptVideos.cached(id) else { return }
            poster = await AttemptVideos.poster(url)
        }
    }
}

struct OutcomePill: View {
    @Environment(DemoStore.self) private var store
    let outcome: CallRecord.Outcome

    var body: some View {
        HStack(spacing: 6) {
            Dot(color: dot)
            Text(verbatim: title).font(.system(size: 13, weight: .semibold))
        }
        .foregroundStyle(ink)
        .padding(.horizontal, 9)
        .frame(height: 24)
        .background(fill, in: Capsule())
        .fixedSize()
    }

    private var title: String {
        switch outcome {
        case .asUsual: String(localized: "As usual")
        case .learning: String(localized: "Still learning")
        case .notUsual: store.pronoun.notUsual
        case .concern: String(localized: "Asked to call")
        case .incomplete: String(localized: "Not finished")
        case .noAnswer: String(localized: "No answer")
        }
    }

    private var calm: Bool { outcome == .asUsual || outcome == .learning }
    private var fill: Color { outcome.needsCall ? Palette.amberCard : calm ? Palette.usualPill : Palette.grey }
    private var ink: Color { outcome.needsCall ? Palette.amberInk : calm ? Palette.usualInk : Palette.secondary }
    private var dot: Color { outcome.needsCall ? Palette.amberDot : calm ? Palette.usualDot : Palette.faint }
}

/// One call: the report, the clip with Save to Photos and Share, the family's note, delete.
struct CallDetailView: View {
    @Environment(DemoStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let recordId: String

    @State private var note = ""
    @State private var frames: AlertClip?
    @State private var confirmDelete = false
    @State private var playing: PlayingVideo?
    /// Counts finished downloads, so the clip thumbnails redraw with their first frame.
    @State private var downloads = 0

    var body: some View {
        Group {
            if let record = store.history.record(recordId) {
                content(record)
            } else {
                Text("This call was removed.")
                    .foregroundStyle(Palette.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Palette.page)
        .foregroundStyle(Palette.ink)
        .navigationTitle(CallRow.when(store.history.record(recordId)?.date ?? .now))
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Palette.page, for: .navigationBar)
    }

    private func content(_ record: CallRecord) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    OutcomePill(outcome: record.outcome)
                    Spacer()
                }
                .padding(EdgeInsets(top: 12, leading: 24, bottom: 14, trailing: 24))

                if record.id == store.currentRecordId, store.phase != .asUsual {
                    Button(action: store.openAlert) {
                        HStack {
                            Text("Open the alert")
                            Spacer()
                            Image(systemName: "chevron.right")
                        }
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Palette.amberInk)
                        .padding(.horizontal, 18)
                        .frame(height: 52)
                        .background(Palette.amberCard, in: RoundedRectangle(cornerRadius: 16))
                        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(Palette.amberOutline, lineWidth: 1.5))
                    }
                    .buttonStyle(PressStyle())
                    .padding(.horizontal, 20)
                    .padding(.bottom, 14)
                }

                if record.clip != nil {
                    clip(record)
                }

                heading("Report")
                card {
                    Text(verbatim: record.text)
                        .font(.system(size: 17))
                        .tracking(-0.4)
                        .lineSpacing(4)
                    if let follow = followUpLine(record) {
                        Text(verbatim: follow)
                            .font(.system(size: 15))
                            .foregroundStyle(Palette.secondary)
                    }
                }

                if let attempts = record.attempts, !attempts.isEmpty {
                    heading("Clips")
                    attemptList(attempts)
                }

                heading("Your note")
                card {
                    TextField("Add a note for the family", text: $note, axis: .vertical)
                        .font(.system(size: 17))
                        .lineLimit(2...8)
                        .onChange(of: note) { _, new in
                            store.history.update(recordId) { $0.note = new }
                        }
                }

                Button(role: .destructive) { confirmDelete = true } label: {
                    Text("Delete this call")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Palette.red)
                        .frame(maxWidth: .infinity)
                        .frame(height: 50)
                        .background(Color.white, in: RoundedRectangle(cornerRadius: 16))
                }
                .buttonStyle(PressStyle())
                .padding(.horizontal, 20)
                .padding(.top, 28)
                .confirmationDialog("Delete this call?", isPresented: $confirmDelete, titleVisibility: .visible) {
                    Button("Delete", role: .destructive) {
                        store.history.delete([recordId])
                        dismiss()
                    }
                } message: {
                    Text("The report, your note and the clip on this phone are removed.")
                }

                if record.outcome.needsCall {
                    Text(verbatim: store.pronoun.unwellLine)
                        .font(.system(size: 15))
                        .foregroundStyle(Palette.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 20)
                }
            }
            .padding(.bottom, 32)
        }
        .scrollDismissesKeyboard(.interactively)
        .onAppear { note = record.note }
        .task(id: recordId) {
            if record.clip == .frames {
                frames = await Task.detached { CallHistory.loadFrames(record) }.value
            }
            await store.refreshAttempts([record])
            // Short silent videos: keep them on this phone, for the thumbnails and after the backend deletes them.
            for clip in store.history.record(recordId)?.attempts ?? [] where clip.status == .ready && !clip.isOnPhone {
                if (try? await store.feed.downloadVideo(clip)) != nil { downloads += 1 }
            }
        }
        .sheet(item: $playing) { video in
            NavigationStack {
                VideoPlayer(player: AVPlayer(url: video.url))
                    .background(Color.black)
                    .navigationTitle(Text(verbatim: video.title))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) { Button("Done") { playing = nil } }
                    }
            }
        }
    }

    /// One row per task attempt, each with its own state ("Smile · try 1", kept for 7 days).
    private func attemptList(_ attempts: [AttemptClip]) -> some View {
        let rows = AttemptClip.numbered(attempts)
        return VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.element.clip.id) { index, row in
                if index > 0 {
                    Rectangle().fill(Palette.divider).frame(height: 0.5).padding(.leading, 78)
                }
                AttemptRow(clip: row.clip, title: String(localized: "\(row.task.short) · try \(row.attempt)")) { url in
                    playing = PlayingVideo(url: url, title: String(localized: "\(row.task.short) · try \(row.attempt)"))
                }
            }
        }
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
        .padding(.horizontal, 20)
        .id(downloads)
    }

    @ViewBuilder
    private func clip(_ record: CallRecord) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            switch record.clip {
            case .staged: ClipPlayerView(height: 230)
            default: FrameClipView(clip: frames, height: 230)
            }
            if ClipExport.hasClip(record) {
                ClipSaveBar(record: record, name: store.name)
            }
        }
        .padding(.horizontal, 20)
    }

    private func followUpLine(_ record: CallRecord) -> String? {
        var parts: [String] = []
        if let t = record.calledAt { parts.append(String(localized: "You called at \(Clock.time(t))")) }
        if let t = record.markedFineAt { parts.append(store.pronoun.saidFine + " " + Clock.time(t)) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func heading(_ title: LocalizedStringKey) -> some View {
        Text(title)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(Palette.secondary)
            .padding(EdgeInsets(top: 22, leading: 24, bottom: 8, trailing: 24))
    }

    private func card<C: View>(@ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 10, content: content)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
            .padding(.horizontal, 20)
    }
}

private struct PlayingVideo: Identifiable {
    let url: URL
    let title: String
    var id: URL { url }
}

/// A task attempt in the call detail. Tapping a playable one downloads it (once) and plays it.
private struct AttemptRow: View {
    @Environment(DemoStore.self) private var store
    let clip: AttemptClip
    let title: String
    let play: (URL) -> Void
    @State private var loading = false

    var body: some View {
        Button {
            guard clip.isPlayable, !loading else { return }
            loading = true
            Task {
                defer { loading = false }
                if let url = try? await store.feed.downloadVideo(clip) { play(url) }
            }
        } label: {
            HStack(spacing: 14) {
                ClipThumb(kind: kind, posterClipId: clip.isOnPhone ? clip.clipId : nil)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: title)
                        .font(.system(size: 17, weight: .semibold))
                        .tracking(-0.4)
                        .foregroundStyle(Palette.ink)
                    Text(verbatim: ClipBadge.line(clip))
                        .font(.system(size: 13))
                        .foregroundStyle(Palette.faint)
                }
                Spacer(minLength: 0)
                if loading { ProgressView() }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(minHeight: 84)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .disabled(!clip.isPlayable)
        .accessibilityElement(children: .combine)
    }

    private var kind: ClipBadge.Kind {
        if clip.isPlayable { return .ready }
        switch clip.status {
        case .preparing: return .preparing
        case .expired: return .expired
        case .failed, .ready: return .failed
        }
    }
}

/// Edit the profile. Nothing changes until Save.
struct EditProfileView: View {
    @Environment(DemoStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var draft = PersonProfile()
    @State private var phone = ""
    @State private var photo: UIImage?
    @State private var photoChanged = false
    @State private var pick: PhotosPickerItem?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Spacer()
                        VStack(spacing: 10) {
                            Avatar(size: 96, label: draft.shownName, photo: photo, fontSize: 12)
                            PhotosPicker(selection: $pick, matching: .images) {
                                Text(photo == nil ? "Add photo" : "Change photo")
                            }
                            if photo != nil {
                                Button("Remove photo", role: .destructive) {
                                    photo = nil
                                    photoChanged = true
                                }
                                .font(.system(size: 15))
                            }
                        }
                        Spacer()
                    }
                    .buttonStyle(.borderless)
                }
                .listRowBackground(Color.clear)

                Section("Name") {
                    TextField("Name", text: $draft.name)
                        .textContentType(.name)
                        .accessibilityIdentifier("profile.name")
                    TextField("Dad, Mum, Aunt…", text: $draft.relation)
                        .accessibilityIdentifier("profile.relation")
                    Picker("Call them", selection: $draft.pronoun) {
                        ForEach(Pronoun.allCases) { Text(verbatim: $0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }

                Section {
                    TextField("+370 600 00000", text: $phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                        .accessibilityIdentifier("profile.phone")
                } header: {
                    Text("Phone number")
                } footer: {
                    Text("Call \(draft.shownName) dials this number. The app never dials 112 or another short number.")
                }

                Section {
                    TextField("Medicines, routine, who has a key…", text: $draft.notes, axis: .vertical)
                        .lineLimit(3...8)
                        .accessibilityIdentifier("profile.notes")
                } header: {
                    Text("Notes")
                }
            }
            .navigationTitle("Edit profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        store.saveProfile(draft, phone: phone, photo: photo, photoChanged: photoChanged)
                        dismiss()
                    }
                    .fontWeight(.semibold)
                }
            }
            .onAppear {
                guard !loaded else { return }
                loaded = true
                draft = store.profile
                phone = store.phoneNumber
                photo = store.photo
            }
            .onChange(of: pick) { _, item in
                guard let item else { return }
                Task {
                    if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                        photo = image.fitted(to: 800)
                        photoChanged = true
                    }
                }
            }
        }
        .tint(Palette.back)
        .preferredColorScheme(.light)
    }
}
