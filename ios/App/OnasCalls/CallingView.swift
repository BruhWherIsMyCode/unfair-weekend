import SwiftUI

/// Design step 5, drawn by us. Used only for:
/// - the fake Fedya call (panel toggle "Fake call screen", or no telephony), 4 s then Declined;
/// - "Call 112", which never dials: 4 s with a visible "DEMO – not dialled" label, then back to the alert.
struct CallingView: View {
    @Environment(DemoStore.self) private var store

    private var is112: Bool { store.overlay == .fake112 }

    var body: some View {
        ZStack {
            Palette.callBg.ignoresSafeArea()
            VStack(spacing: 20) {
                Avatar(size: 132, label: is112 ? "" : "Fedya",
                       photo: is112 ? nil : LocalAssets.fedyaPhoto,
                       stripes: Palette.callAvatarStripes, textColor: Palette.callAvatarText, fontSize: 11)
                VStack(spacing: 6) {
                    Text(verbatim: is112 ? "112" : "Fedya")
                        .font(.system(size: 34, weight: .semibold))
                        .tracking(-0.6)
                    Text("calling…")
                        .font(.system(size: 19))
                        .opacity(0.75)
                    if is112 {
                        Text("DEMO – not dialled")
                            .font(.system(size: 15, weight: .semibold))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(Color.white.opacity(0.16), in: Capsule())
                            .padding(.top, 10)
                    }
                }
            }
            .frame(maxHeight: .infinity, alignment: .top)
            .padding(.top, 88)

            HStack(spacing: 52) {
                Circle().fill(Color.white.opacity(0.18)).frame(width: 76, height: 76)
                Circle().fill(Color.white.opacity(0.18)).frame(width: 76, height: 76)
                Button(action: store.endFakeCall) {
                    Circle()
                        .fill(Palette.callEnd)
                        .frame(width: 76, height: 76)
                        .overlay(PhoneGlyph().fill(.white).frame(width: 30, height: 30).rotationEffect(.degrees(135)))
                }
                .accessibilityLabel(Text("End"))
            }
            .frame(maxHeight: .infinity, alignment: .bottom)
            .padding(.bottom, 86)
        }
        .foregroundStyle(.white)
        .contentShape(Rectangle())
        // Design: tapping anywhere on the Fedya calling screen ends the call.
        .onTapGesture { if !is112 { store.endFakeCall() } }
    }
}
