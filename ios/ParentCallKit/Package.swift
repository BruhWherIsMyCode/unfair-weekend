// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ParentCallKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "ParentCallKit", targets: ["ParentCallKit"]),
        .executable(name: "parent-call-sim", targets: ["ParentCallSim"]),
    ],
    targets: [
        .target(name: "ParentCallKit"),
        .executableTarget(name: "ParentCallSim", dependencies: ["ParentCallKit"]),
        .testTarget(name: "ParentCallKitTests", dependencies: ["ParentCallKit"]),
    ]
)
