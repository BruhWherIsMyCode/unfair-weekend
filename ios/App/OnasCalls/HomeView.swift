import SwiftUI

/// Design steps 3 (alert above Fedya) and 6 (declined), plus the "as usual" state.
struct HomeView: View {
    @Environment(DemoStore.self) private var store
    @State private var showPanel = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                Spacer().frame(height: 24)
                switch store.phase {
                case .alert, .calling:
                    fedyaGroup { AlertCard() } row: {
                        FedyaRow(subtitle: store.profile.lastCall(Clock.time(store.alertTime)))
                    }
                case .declined:
                    fedyaGroup { DeclinedCard() } row: {
                        FedyaRow(subtitle: store.profile.youCalled(Clock.time(store.calledAt ?? .now)))
                    }
                case .asUsual:
                    asUsualCard
                }
            }
            .padding(.bottom, 32)
        }
        .scrollBounceBehavior(.basedOnSize)
        .background(Palette.page)
        .foregroundStyle(Palette.ink)
        .sheet(isPresented: $showPanel) { DemoPanelView() }
    }

    private var header: some View {
        TimelineView(.everyMinute) { ctx in
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: Clock.date(ctx.date))
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.secondary)
                Text("Family")
                    .font(.system(size: 40, weight: .bold))
                    .tracking(-1)
                    // Hidden demo panel: hold the title for 2 seconds.
                    .onLongPressGesture(minimumDuration: 2) {
                        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
                        showPanel = true
                    }
            }
        }
        .padding(.horizontal, 24)
        .padding(.top, 44) // design: 44pt between the status bar and the date
    }

    /// Amber card on top, Fedya's white row under it, both outlined. The row opens the profile.
    private func fedyaGroup<Card: View, Row: View>(@ViewBuilder card: () -> Card, @ViewBuilder row: () -> Row) -> some View {
        VStack(spacing: 0) {
            card()
            Button(action: store.openProfile) {
                row()
                    .background(Color.white)
                    .clipShape(UnevenRoundedRectangle(bottomLeadingRadius: 22, bottomTrailingRadius: 22))
                    .overlay(UnevenRoundedRectangle(bottomLeadingRadius: 22, bottomTrailingRadius: 22)
                        .strokeBorder(Palette.amberOutline, lineWidth: 1.5))
            }
            .buttonStyle(PressStyle())
        }
        .padding(.horizontal, 20)
    }

    /// Everyone as usual: Fedya's row alone. Opens the profile.
    private var asUsualCard: some View {
        Button(action: store.openProfile) {
            PersonRow(name: store.name, subtitle: store.profile.lastCall(store.usualTime),
                      time: store.usualTime, avatarLabel: store.name, photo: store.photo)
                .padding(.leading, 20)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 22))
                .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .padding(.horizontal, 20)
    }
}

/// Step 3 amber card: "NOT HIS USUAL / Call Fedya now / Same on both tries / 10 second clip".
private struct AlertCard: View {
    @Environment(DemoStore.self) private var store

    var body: some View {
        Button(action: store.openAlert) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    NotUsualLabel()
                    Spacer()
                    Text(verbatim: Clock.time(store.alertTime))
                        .font(.system(size: 15).monospacedDigit())
                        .foregroundStyle(Palette.amberTime)
                }
                Text(verbatim: store.callNow)
                    .font(.system(size: 30, weight: .bold))
                    .tracking(-0.7)
                HStack(spacing: 14) {
                    ClipThumbnail(live: store.liveMessage != nil, first: store.clip?.frames.first)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Same on both tries").font(.system(size: 15, weight: .semibold))
                        Group {
                            if store.liveMessage == nil {
                                Text("10 second clip")
                            } else if let clip = store.clip {
                                Text("\(clip.seconds) second clip")
                            } else {
                                Text("Open the report")
                            }
                        }
                        .font(.system(size: 13)).foregroundStyle(Palette.amberTime)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(Palette.amberLabel)
                }
                .padding(.top, 4)
            }
            .foregroundStyle(Palette.amberInk)
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.amberCard)
            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 28, topTrailingRadius: 28))
            .overlay(UnevenRoundedRectangle(topLeadingRadius: 28, topTrailingRadius: 28)
                .strokeBorder(Palette.amberOutline, lineWidth: 1.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
    }
}

/// Step 6 card: "Declined at HH:MM / Call Fedya again / [Call Fedya]".
private struct DeclinedCard: View {
    @Environment(DemoStore.self) private var store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                NotUsualLabel()
                Spacer()
                Text("Declined at \(Clock.time(store.declinedAt ?? .now))")
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.amberTime)
            }
            Text("Call \(store.name) again")
                .font(.system(size: 30, weight: .bold))
                .tracking(-0.7)
            Button(action: store.callFedya) {
                HStack(spacing: 10) {
                    PhoneGlyph().fill(.white).frame(width: 20, height: 20)
                    Text("Call \(store.name)")
                }
                .font(.system(size: 19, weight: .semibold))
                .tracking(-0.4)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .frame(height: 56)
                .background(Palette.green, in: RoundedRectangle(cornerRadius: 18))
            }
            .buttonStyle(PressStyle())
            .padding(.top, 4)
            Text(verbatim: store.pronoun.unwellLine)
                .font(.system(size: 15))
                .frame(maxWidth: .infinity)
        }
        .foregroundStyle(Palette.amberInk)
        .padding(22)
        .background(Palette.amberCard)
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 28, topTrailingRadius: 28))
        .overlay(UnevenRoundedRectangle(topLeadingRadius: 28, topTrailingRadius: 28)
            .strokeBorder(Palette.amberOutline, lineWidth: 1.5))
    }
}

/// Fedya's white row under the card (no status pill). Opens the profile.
private struct FedyaRow: View {
    @Environment(DemoStore.self) private var store
    var subtitle: String
    var body: some View {
        HStack(spacing: 14) {
            Avatar(size: 52, label: store.name, photo: store.photo)
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: store.name).font(.system(size: 20, weight: .semibold)).tracking(-0.4)
                Text(verbatim: subtitle).font(.system(size: 15)).foregroundStyle(Palette.secondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Palette.faint)
        }
        .foregroundStyle(Palette.ink)
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .contentShape(Rectangle())
    }
}

/// Fedya's row in the calm state, with the green pill.
private struct PersonRow: View {
    var name: String
    var subtitle: String
    var time: String
    var avatarLabel = ""
    var photo: UIImage? = nil

    var body: some View {
        HStack(spacing: 14) {
            Avatar(size: 52, label: avatarLabel, photo: photo)
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: name).font(.system(size: 20, weight: .semibold)).tracking(-0.4)
                Text(verbatim: subtitle)
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            Spacer(minLength: 0)
            AsUsualBadge(time: time)
        }
        .padding(.vertical, 16)
        .padding(.trailing, 20)
    }
}

/// 112x64 clip thumbnail with a play button.
private struct ClipThumbnail: View {
    /// A real alert shows its own first frame (or stripes), never the staged clip.
    var live = false
    var first: UIImage?
    @State private var frame: UIImage?

    var body: some View {
        ZStack {
            if let image = live ? first : frame {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Stripes(colors: Palette.thumbStripes, band: 6)
            }
            Circle()
                .fill(Color(hex: 0x1F1B16, opacity: 0.72))
                .frame(width: 30, height: 30)
                .overlay(Image(systemName: "play.fill").font(.system(size: 11)).foregroundStyle(.white).offset(x: 1))
        }
        .frame(width: 112, height: 64)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .task { frame = await LocalAssets.clipThumbnail() }
    }
}

/// Slight dim on press, no other styling.
struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.75 : 1)
    }
}
