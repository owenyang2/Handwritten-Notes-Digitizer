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
    ],
    targets: [
        .target(
            name: "PersonalizedNotesKit",
            path: "Sources/PersonalizedNotesKit",
            linkerSettings: [
                .linkedLibrary("sqlite3"),
            ]
        ),
    ]
)
