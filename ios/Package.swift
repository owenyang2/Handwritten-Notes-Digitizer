// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "PersonalizedNotes",
    defaultLocalization: "en",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(
            name: "PersonalizedNotesKit",
            targets: ["PersonalizedNotesKit"]
        ),
        .executable(
            name: "PersonalizedNotesApp",
            targets: ["PersonalizedNotesApp"]
        ),
    ],
    targets: [
        .target(
            name: "PersonalizedNotesKit",
            path: "Sources/PersonalizedNotesKit",
            linkerSettings: [
                .linkedLibrary("sqlite3"),
            ]
        ),
        .executableTarget(
            name: "PersonalizedNotesApp",
            dependencies: ["PersonalizedNotesKit"],
            path: "Sources/PersonalizedNotesApp"
        ),
        .testTarget(
            name: "PersonalizedNotesKitTests",
            dependencies: ["PersonalizedNotesKit"],
            path: "Tests/PersonalizedNotesKitTests"
        ),
    ]
)
