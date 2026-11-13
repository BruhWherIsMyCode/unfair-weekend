import SwiftUI

/// Design step 4: the clip, the report and the three choices.
struct AlertView: View {
    @Environment(DemoStore.self) private var store

    /// The design frame is 956pt tall. On shorter phones the clip gives up the difference,
    /// so the report and all three buttons stay on screen without scrolling.
    private static let designHeight: CGFloat = 956

    var body: some View {
        GeometryReader { geo in
            let screen = geo.size.height + geo.safeAreaInsets.top + geo.safeAreaInsets.bottom
            content(clipHeight: min(250, max(160, 250 - (Self.designHeight - screen))))
        }
    }

    private func content(clipHeight: CGFloat) -> some View {
        VStack(spacing: 0) {
            backBar
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    who
                    Group {
                        if store.liveMessage != nil {
                            FrameClipView(clip: store.clip, height: clipHeight)
                        } else {
                            ClipPlayerView(height: clipHeight)
                        }
                    }
                    .padding(.horizontal, 20)
                    VStack(alignment: .leading, spacing: 8) {
                        NotUsualLabel()
                        Text("Call Fedya now")
                            .font(.system(size: 30, weight: .bold))
                            .tracking(-0.7)
                            .foregroundStyle(Palette.amberInk)
                    }
                    .padding(EdgeInsets(top: 22, leading: 24, bottom: 0, trailing: 24))
                    Text("Report")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.secondary)
                        .padding(EdgeInsets(top: 22, leading: 24, bottom: 8, trailing: 24))
                    report
                }
                .padding(.bottom, 20)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .foregroundStyle(Palette.ink)
        .background(Palette.page)
        .safeAreaInset(edge: .bottom, spacing: 0) { choices }
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
        }
        .padding(.horizontal, 12)
    }

    private var who: some View {
        HStack(spacing: 14) {
            Avatar(size: 48, label: "Fedya", photo: LocalAssets.fedyaPhoto)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: "Fedya").font(.system(size: 22, weight: .bold)).tracking(-0.5)
                Text(store.liveMessage == nil ? "Check-in at \(Clock.time(store.alertTime)), 1 min 40 s"
                                              : "Check-in at \(Clock.time(store.alertTime))")
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.secondary)
            }
        }
        .padding(EdgeInsets(top: 8, leading: 24, bottom: 18, trailing: 24))
    }

    private var report: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let message = store.liveMessage {
                // The backend writes the family text; shown as is.
                Text(verbatim: message.text)
            } else {
                Text("Fedya seemed different from his usual during today's check-in.")
                Text("He says he took his medicine and feels a bit tired.")
                    .foregroundStyle(Palette.secondary)
            }
        }
        .font(.system(size: 17))
        .tracking(-0.4)
        .lineSpacing(4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
        .padding(.horizontal, 20)
    }

    private var choices: some View {
        VStack(spacing: 10) {
            Button(action: store.callFedya) {
                HStack(spacing: 10) {
                    PhoneGlyph().fill(.white).frame(width: 22, height: 22)
                    Text("Call Fedya")
                }
                .font(.system(size: 21, weight: .semibold))
                .tracking(-0.4)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .frame(height: 64)
                .background(Palette.green, in: RoundedRectangle(cornerRadius: 20))
                // The design's 6pt ring on the main action.
                .background(RoundedRectangle(cornerRadius: 26).fill(Palette.green.opacity(0.25)).padding(-6))
            }
            .buttonStyle(PressStyle())
            HStack(spacing: 10) {
                Button(action: store.fakeCall112) {
                    Text("Call 112")
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .frame(height: 54)
                        .background(Palette.red, in: RoundedRectangle(cornerRadius: 18))
                }
                Button(action: store.heIsFine) {
                    Text("He's fine")
                        .foregroundStyle(Palette.ink)
                        .frame(maxWidth: .infinity)
                        .frame(height: 54)
                        .background(Palette.grey, in: RoundedRectangle(cornerRadius: 18))
                }
            }
            .font(.system(size: 17, weight: .semibold))
            .tracking(-0.4)
            .buttonStyle(PressStyle())
            Text("If he seems unwell, call 112.")
                .font(.system(size: 15))
                .foregroundStyle(Palette.secondary)
                .padding(.top, 4)
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 12)
        .background(alignment: .top) {
            Palette.page
                .overlay(alignment: .top) { Rectangle().fill(Palette.divider).frame(height: 0.5) }
                .ignoresSafeArea(edges: .bottom)
        }
    }
}
