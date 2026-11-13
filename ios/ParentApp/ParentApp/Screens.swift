import SwiftUI

/// Design screen 1: the incoming call. In-app lookalike of the system call screen (staged: real CallKit
/// needs a VoIP push, which the demo does not have).
struct IncomingView: View {
    let title: String
    let onAccept: () -> Void
    let onDecline: () -> Void

    @State private var ringingSince = Date()
    @State private var answered = false
    @State private var pressed = false

    var body: some View {
        ZStack {
            Palette.ringGradient

            VStack(spacing: 4) {
                Text("video call")
                    .font(.system(size: 21))
                    .foregroundStyle(Color.white.opacity(0.55))
                Text(title)
                    .font(.system(size: 34, weight: .bold))
                    .tracking(-0.5)
            }
            .padding(.top, 124)
            .frame(maxHeight: .infinity, alignment: .top)

            HStack(alignment: .top) {
                callButton(label: "Decline", color: Palette.decline, hangUp: true, scale: 1) {
                    guard !answered else { return }
                    answered = true
                    onDecline()
                }
                Spacer()
                accept
            }
            .padding(.horizontal, 44)
            .padding(.bottom, 72)
            .frame(maxHeight: .infinity, alignment: .bottom)
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
    }

    /// Accept pulses and sends out a halo while ringing: one of the two loops.
    private var accept: some View {
        TimelineView(.animation(paused: answered)) { timeline in
            let t = timeline.date.timeIntervalSince(ringingSince)
            let pulse = answered ? 1 : 1 + 0.06 * max(0, sin(t * 5))
            let halo = answered ? 1 : (t * 0.9).truncatingRemainder(dividingBy: 1)
            callButton(label: "Accept", color: Palette.accept, hangUp: false, scale: pulse * (pressed ? 0.9 : 1)) {
                guard !answered else { return }
                answered = true
                pressed = true
                onAccept()
                Task {
                    try? await Task.sleep(nanoseconds: 300_000_000)
                    pressed = false
                }
            }
            .background(alignment: .top) {
                Circle()
                    .strokeBorder(Color(hex: 0x2BA246, opacity: 0.8), lineWidth: 2)
                    .frame(width: 88 * (1 + halo * 0.7), height: 88 * (1 + halo * 0.7))
                    .frame(width: 88, height: 88)
                    .opacity(answered ? 0 : 1 - halo)
            }
        }
    }

    private func callButton(label: String, color: Color, hangUp: Bool, scale: Double,
                            action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 12) {
                Circle()
                    .fill(color)
                    .frame(width: 88, height: 88)
                    .overlay(PhoneIcon(size: 38, hangUp: hangUp))
                    .scaleEffect(scale)
                Text(label)
                    .font(.system(size: 19))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Design screen 9: "Thank you". The check pops in, then the text fades in. After about 3 s, or on a tap,
/// the app leaves to the iPhone home screen.
struct DoneView: View {
    let onDismiss: () -> Void

    @State private var check = false
    @State private var text = false

    var body: some View {
        ZStack {
            Palette.phone

            VStack(spacing: 36) {
                Circle()
                    .fill(Palette.green.opacity(0.16))
                    .frame(width: 120, height: 120)
                    .overlay(
                        CheckMark()
                            .trim(from: 0, to: check ? 1 : 0)
                            .stroke(Palette.green, style: StrokeStyle(lineWidth: 2.6 * 56 / 24, lineCap: .round,
                                                                      lineJoin: .round))
                            .frame(width: 56, height: 56)
                    )
                    .scaleEffect(check ? 1 : 0)

                VStack(spacing: 14) {
                    Text("Thank you")
                        .font(.system(size: 40, weight: .bold))
                        .tracking(-0.8)
                    Text("Next call at 18:00")
                        .font(.system(size: 24))
                        .foregroundStyle(Color.white.opacity(0.75))
                }
                .opacity(text ? 1 : 0)
            }
            .multilineTextAlignment(.center)
            .padding(.top, 250)
            .frame(maxHeight: .infinity, alignment: .top)
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
        .contentShape(Rectangle())
        .onTapGesture(perform: onDismiss)
        .onAppear {
            withAnimation(Motion.pop(0.6).delay(0.5)) { check = true }
            withAnimation(Motion.enter(0.7).delay(0.9)) { text = true }
        }
        .task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled {
                onDismiss()
            }
        }
    }
}

/// Between calls (not in the design). Long-press for 2 s anywhere opens the hidden settings.
struct IdleView: View {
    let connection: AppState.Connection
    let onSettings: () -> Void

    var body: some View {
        ZStack {
            Palette.phone

            VStack(spacing: 14) {
                Text("Daily check-in")
                    .font(.system(size: 34, weight: .bold))
                    .tracking(-0.5)
                Text("Next call at 18:00")
                    .font(.system(size: 24))
                    .foregroundStyle(Color.white.opacity(0.75))
            }

            Text(statusLine)
                .font(.system(size: 15))
                .foregroundStyle(Color.white.opacity(0.4))
                .padding(.bottom, 56)
                .frame(maxHeight: .infinity, alignment: .bottom)
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
        .contentShape(Rectangle())
        .onLongPressGesture(minimumDuration: 2, perform: onSettings)
    }

    private var statusLine: String {
        switch connection {
        case .notSetUp: return "Not set up yet"
        case .connecting: return "Connecting\u{2026}"
        case .waiting: return ""
        }
    }
}

/// Hidden settings: backend address, demo key, person id. Stored on this phone only.
struct SettingsView: View {
    let initial: AppSettings
    let onSave: (AppSettings) -> Void
    /// Opens the camera debug screen (after this sheet closes).
    var onCameraDebug: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @State private var draft: AppSettings

    init(initial: AppSettings, onSave: @escaping (AppSettings) -> Void, onCameraDebug: @escaping () -> Void = {}) {
        self.initial = initial
        self.onSave = onSave
        self.onCameraDebug = onCameraDebug
        _draft = State(initialValue: initial)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.20:8000", text: $draft.baseURL)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Backend address")
                } footer: {
                    if !draft.baseURL.isEmpty && draft.url == nil {
                        Text("Use http://<mac-ip>:8000")
                    }
                }
                Section("Demo key") {
                    TextField("DEMO_KEY", text: $draft.demoKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section("Person") {
                    TextField("p_demo", text: $draft.personId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    Button("Camera debug") {
                        onCameraDebug()
                        dismiss()
                    }
                    .disabled(!initial.isComplete)
                } header: {
                    Text("Debug")
                } footer: {
                    Text("Runs the real smile or arms analysis on this camera, with no call and no assistant, and shows what it sees. Nothing is stored.")
                }
                Section("This phone") {
                    Text(UserDefaults.standard.string(forKey: "parent.device_id") ?? "not registered yet")
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onSave(draft)
                        dismiss()
                    }
                    .disabled(!draft.isComplete)
                }
            }
        }
    }
}
