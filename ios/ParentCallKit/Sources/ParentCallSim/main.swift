import Foundation
import ParentCallKit

// Runs the real CallClient against a running backend with fake devices, answers the first ring and prints
// every phase until the call ends.  Usage:  DEMO_KEY=... parent-call-sim http://127.0.0.1:8000 p_demo [scale]

let args = CommandLine.arguments
guard args.count >= 3, let base = URL(string: args[1]) else {
    FileHandle.standardError.write(Data("usage: parent-call-sim BASE_URL PERSON_ID [TIME_SCALE]\n".utf8))
    exit(2)
}
let key = ProcessInfo.processInfo.environment["DEMO_KEY"] ?? ""
let scale = args.count > 3 ? Double(args[3]) ?? 1.0 : 1.0

final class Box: @unchecked Sendable {
    var client: CallClient?
    let done = DispatchSemaphore(value: 0)
}

let box = Box()
let api = CallAPI(baseURL: base, demoKey: key)
let client = CallClient(api: api, deviceId: "dev_sim_parent", personId: args[2],
                        devices: FakeDevices.make(timeScale: scale)) { phase in
    print("PHASE \(phase)")
    fflush(stdout)
    switch phase {
    case .ringing:
        Task {
            try? await box.client?.answer()
        }
    case .ended:
        box.done.signal()
    default:
        break
    }
}
box.client = client

Task {
    do {
        try await client.start()
        print("REGISTERED")
        fflush(stdout)
    } catch {
        print("START FAILED \(error)")
        box.done.signal()
    }
}

let waited = box.done.wait(timeout: .now() + 300)
print(waited == .success ? "DONE" : "TIMEOUT")
exit(waited == .success ? 0 : 1)
