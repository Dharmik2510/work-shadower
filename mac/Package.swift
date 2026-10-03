// swift-tools-version:5.9
// Dot — the macOS client for Work Shadower.
//
//   DotCore  platform-neutral logic (models, redaction, cleanup, templates,
//            target scoring, SQLite queue, API client, uploader). Builds and
//            tests on Linux too.
//   DotApp   the AppKit/SwiftUI app (macOS 13+). On non-macOS platforms it
//            compiles to a stub so `swift test` works everywhere.
import PackageDescription

let package = Package(
    name: "Dot",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "Dot", targets: ["DotApp"]),
    ],
    targets: [
        // Linux only: exposes the system libsqlite3 (macOS uses `import SQLite3`).
        .systemLibrary(name: "CSQLite", path: "Sources/CSQLite"),
        .target(
            name: "DotCore",
            dependencies: [
                .target(name: "CSQLite", condition: .when(platforms: [.linux])),
            ]
        ),
        .executableTarget(
            name: "DotApp",
            dependencies: ["DotCore"]
        ),
        .testTarget(
            name: "DotCoreTests",
            dependencies: ["DotCore"]
        ),
    ]
)
