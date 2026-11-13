import Foundation

public enum Resource: String, Sendable {
    case camera
    case microphone
}

public enum ResourceProblem: String, Sendable {
    case permissionDenied = "permission_denied"
    case trackEnded = "track_ended"
    case deviceMissing = "device_missing"
    case unknown
}

public struct CaptureSettings: Decodable, Sendable, Equatable {
    public let mediaKind: String
    public let durationMs: Int
    public let prepareMs: Int
    public let stopOnSilenceMs: Int?
    public let format: String?
    public let samplePeriodMs: Int?
}

public struct CapturePurpose: Decodable, Sendable, Equatable {
    public let kind: String
    public let questionId: String?
    public let task: String?
}

public struct SayCommand: Decodable, Sendable, Equatable {
    public let commandId: String
    public let lineId: String
    public let text: String
    public let audioPath: String
    public let durationMs: Int
}

public struct CaptureCommand: Decodable, Sendable, Equatable {
    public let commandId: String
    public let purpose: CapturePurpose
    public let settings: CaptureSettings
    public let transport: String
}

public enum ClientCommand: Decodable, Sendable, Equatable {
    case say(SayCommand)
    case capture(CaptureCommand)

    private enum Keys: String, CodingKey {
        case kind
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: Keys.self)
        let kind = try container.decode(String.self, forKey: .kind)
        if kind == "say" {
            self = .say(try SayCommand(from: decoder))
            return
        }
        if kind == "capture" {
            self = .capture(try CaptureCommand(from: decoder))
            return
        }
        throw DecodingError.dataCorruptedError(forKey: .kind, in: container, debugDescription: "Unknown command kind")
    }

    public var commandId: String {
        switch self {
        case .say(let say):
            return say.commandId
        case .capture(let capture):
            return capture.commandId
        }
    }
}

public struct CommandExecuteEvent: Decodable, Sendable {
    public let eventId: String
    public let callId: String
    public let command: ClientCommand
}

public struct CommandCancelEvent: Decodable, Sendable {
    public let eventId: String
    public let callId: String
    public let commandId: String
    public let reason: String
}

public struct TaskProgressEvent: Decodable, Sendable {
    public let eventId: String
    public let callId: String
    public let attemptId: String
    public let elapsedMs: Int
    public let phase: String
    public let inputQuality: String
    public let hint: String?
}

public struct IncomingCallEvent: Decodable, Sendable {
    public let callId: String
    public let callerName: String
    public let personName: String?
    public let ringTimeoutS: Double
}

public struct CallEndedEvent: Decodable, Sendable {
    public let callId: String
    public let reason: String?
}

public struct AnswerResponse: Decodable, Sendable {
    public let callId: String
    public let personName: String
    public let voiced: Bool
}

/// What the parent screen shows. It never carries a measurement or a verdict.
public enum CallPhase: Sendable, Equatable {
    case idle
    case ringing(callId: String, callerName: String)
    case connecting
    case speaking(caption: String)
    case listening(questionId: String)
    case task(name: String, secondsLeft: Int, hint: String?)
    case finishing
    case ended(reason: String?)
}

enum JSON {
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    static func encode(_ object: [String: Any?]) throws -> Data {
        var plain: [String: Any] = [:]
        for (key, value) in object {
            if let value {
                plain[key] = value
            } else {
                plain[key] = NSNull()
            }
        }
        return try JSONSerialization.data(withJSONObject: plain, options: [.sortedKeys])
    }
}
