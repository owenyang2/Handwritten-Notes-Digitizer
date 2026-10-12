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
        .library(
            name: "ShareInbox",
            targets: ["ShareInbox"]
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
        .target(
            name: "ShareInbox",
            path: "Sources/ShareExtension",
            exclude: [
                "Info.plist",
                "ShareExtension.entitlements",
            ]
        ),
        .executableTarget(
            name: "PersonalizedNotesApp",
            dependencies: [
                "PersonalizedNotesKit",
                "ShareInbox",
            ],
            path: "Sources/PersonalizedNotesApp",
            exclude: [
                "Info.plist",
                "PersonalizedNotes.entitlements",
            ]
        ),
        .testTarget(
            name: "PersonalizedNotesKitTests",
            dependencies: [
                "PersonalizedNotesKit",
            ],
            path: "Tests/PersonalizedNotesKitTests"
        ),
    ]
)
