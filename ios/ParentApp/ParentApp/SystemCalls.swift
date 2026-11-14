import AVFoundation
import CallKit
import Foundation

/// The real iOS incoming-call screen, for a call that arrives while the app is in the background.
///
/// Demo workaround, not for the App Store: there is no VoIP push. The app stays running in the background on a
/// silent audio loop (`BackgroundKeepAlive`), hears `call.incoming` on its open event stream, and reports the call
/// to CallKit itself. In the foreground the app's own ring screen is used instead.
@MainActor
final class SystemCalls: NSObject {
    var onAnswer: () -> Void = {}
    var onDecline: () -> Void = {}
    var onHangUp: () -> Void = {}
    /// The system took the audio session away (for example when a CallKit call ends).
    var onAudioDeactivated: () -> Void = {}

    private let provider: CXProvider
    private var current: UUID?
    private var answered = false

    override init() {
        let configuration = CXProviderConfiguration()
        configuration.supportsVideo = true
        configuration.maximumCallGroups = 1
        configuration.maximumCallsPerCallGroup = 1
        configuration.supportedHandleTypes = [.generic]
        configuration.includesCallsInRecents = false
        provider = CXProvider(configuration: configuration)
        super.init()
        provider.setDelegate(self, queue: .main)
    }

    var isRinging: Bool { current != nil && !answered }

    func reportIncoming(title: String) {
        guard current == nil else { return }
        let id = UUID()
        current = id
        answered = false
        let update = CXCallUpdate()
        update.remoteHandle = CXHandle(type: .generic, value: title)
        update.localizedCallerName = title
        update.hasVideo = true
        update.supportsHolding = false
        update.supportsGrouping = false
        update.supportsUngrouping = false
        update.supportsDTMF = false
        provider.reportNewIncomingCall(with: id, update: update) { [weak self] error in
            guard error != nil else { return }
            Task { @MainActor in
                if self?.current == id {
                    self?.current = nil
                }
            }
        }
    }

    /// The call ended on our side (done, hang-up, timeout, lost connection): take it off the system UI.
    func reportEnded(unanswered: Bool) {
        guard let id = current else { return }
        current = nil
        provider.reportCall(with: id, endedAt: nil, reason: unanswered ? .unanswered : .remoteEnded)
    }
}

extension SystemCalls: CXProviderDelegate {
    nonisolated func providerDidReset(_ provider: CXProvider) {
        MainActor.assumeIsolated {
            current = nil
            answered = false
        }
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXAnswerCallAction) {
        MainActor.assumeIsolated {
            guard action.callUUID == current else {
                action.fail()
                return
            }
            answered = true
            onAnswer()
            action.fulfill()
        }
    }

    nonisolated func provider(_ provider: CXProvider, didActivate audioSession: AVAudioSession) {
        // Nothing needed: the call's audio session is shared with the app's own players.
    }

    nonisolated func provider(_ provider: CXProvider, didDeactivate audioSession: AVAudioSession) {
        // The system deactivates the session without any interruption notice: the silent loop stops with it.
        MainActor.assumeIsolated {
            onAudioDeactivated()
        }
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXEndCallAction) {
        MainActor.assumeIsolated {
            guard action.callUUID == current else {
                action.fulfill()
                return
            }
            current = nil
            if answered {
                onHangUp()
            } else {
                onDecline()
            }
            action.fulfill()
        }
    }
}
