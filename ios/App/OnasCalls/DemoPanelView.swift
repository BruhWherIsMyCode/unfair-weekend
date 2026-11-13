import SwiftUI

/// Hidden operator panel: hold "Family" for 2 seconds. Settings are kept in UserDefaults.
struct DemoPanelView: View {
    @Environment(DemoStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        @Bindable var store = store
        @Bindable var feed = store.feed
        NavigationStack {
            Form {
                Section {
                    TextField("+370 600 00000", text: $store.phoneNumber)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                } header: {
                    Text("Fedya's phone number")
                } footer: {
                    if store.fedyaDigits.isEmpty {
                        Text("No number set: Call Fedya shows the fake call screen.")
                    } else {
                        Text("Call Fedya dials \(store.fedyaDigits). The only number the app ever dials.")
                    }
                }

                Section("Alert delay") {
                    Picker("Alert delay", selection: $store.delaySeconds) {
                        ForEach(DemoStore.delayPresets, id: \.self) { s in
                            Text("\(s) s").tag(s)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                Section {
                    Toggle("Fake call screen", isOn: $store.fakeCallScreen)
                } footer: {
                    Text("For no signal on stage. Call Fedya shows the calling screen for 4 s, then Declined. Nothing is dialled.")
                }

                Section {
                    Button("Arm") {
                        store.arm()
                        dismiss()
                    }
                    .fontWeight(.semibold)
                    Button("Show alert now") {
                        store.showAlertNow()
                        dismiss()
                    }
                    Button("Reset", role: .destructive) { store.reset() }
                } footer: {
                    Text("Arm: notification after the delay, app in the alert state. Lock the phone after arming.")
                }

                Section("Status") {
                    LabeledContent("Scene", value: phaseName)
                    if let armed = store.armedFor {
                        LabeledContent("Notification at", value: armed.formatted(date: .omitted, time: .standard))
                    }
                    LabeledContent("Notifications", value: allowedText(store.notificationsAllowed))
                    LabeledContent("Time Sensitive", value: allowedText(store.timeSensitiveAllowed))
                }

                Section {
                    TextField("http://192.168.1.20:8000", text: $feed.baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Demo key", text: $feed.demoKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("f_demo", text: $feed.familyId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Connect") { store.connect() }
                        .disabled(!feed.isConfigured)
                    LabeledContent("Backend", value: feedStatus)
                } header: {
                    Text("Check-in backend")
                } footer: {
                    Text("Alerts from the real check-in call arrive here, with her clip. Leave empty for the staged scene only. Keep the app open once after connecting: it then keeps listening in the background.")
                }
            }
            .navigationTitle("Demo panel")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .onAppear { store.refreshNotificationSettings() }
        }
        .preferredColorScheme(.light)
    }

    private var feedStatus: String {
        switch store.feed.status {
        case .off: String(localized: "Not set up")
        case .connecting: String(localized: "Connecting…")
        case .listening: String(localized: "Listening")
        case .failed(let reason): reason
        }
    }

    private var phaseName: String {
        switch store.phase {
        case .asUsual: String(localized: "As usual")
        case .alert: String(localized: "Alert")
        case .calling: String(localized: "Calling")
        case .declined: String(localized: "Declined")
        }
    }

    private func allowedText(_ value: Bool?) -> String {
        switch value {
        case .some(true): String(localized: "On")
        case .some(false): String(localized: "Off")
        case .none: "…"
        }
    }
}
