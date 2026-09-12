# iPad alpha source

This directory is the **iPad alpha** of Personalized Notes, not a finished App Store build.

## What is here

- `Sources/PersonalizedNotesKit` — shared Swift 6 package: `ink_v1`, SQLite local store, pending-operation sync engine, HTTP client, session vault.
- `Sources/PersonalizedNotesApp` — SwiftUI shell (`@main`), local-first library, PencilKit canvas, account sessions, settings.
- `Sources/ShareExtension` — share-sheet importer for `public.image` and `com.adobe.pdf`. It writes a pending import into the App Group container and does not upload.
- `Tests/PersonalizedNotesKitTests` — macOS-safe XCTest coverage for ink round-trip, local-store crash recovery, and the sync engine with a fake transport.
- `Fixtures/ink_v1_simple.json` — golden `ink_v1` document used by kit tests.
- `Package.swift` — SPM products: library `PersonalizedNotesKit`, library `ShareInbox`, executable `PersonalizedNotesApp`, test target `PersonalizedNotesKitTests`.
- `project.yml` — XcodeGen spec for the real iPad app + share extension.

## What still requires a physical iPad

SPM can type-check the SwiftUI app on macOS by substituting `CanvasUnavailableView` wherever `PencilKit`/`UIKit` are missing. That is **not** a substitute for device testing. Apple Pencil only-draw, finger pan/zoom, `PKToolPicker`, background fetch/processing, the share extension, Keychain, and the App Group all need an iPad (or at least a signed iPad simulator) built from the XcodeGen project.

The App Group identifier is the documented placeholder:

```
group.com.handwrittennotes.digitizer
```

Create that App Group on the Apple Developer team and enable it on both the app (`com.handwrittennotes.digitizer`) and the extension (`com.handwrittennotes.digitizer.share`) before running on a device. Without signing, `containerURL(forSecurityApplicationGroupIdentifier:)` returns nil; the host app may fall back to Application Support so kit-side ingest can be exercised, but the share extension will fail closed.

## Tests vs. the app target

| Command | What it covers |
| --- | --- |
| `cd ios && swift test` | `PersonalizedNotesKitTests` only. No UIKit. Ink, SQLite pending operations, `SyncEngine` against an in-memory fake. |
| `xcodegen generate` then Xcode | iPad app + share extension. Requires XcodeGen, an Apple team, and a device or simulator. |

`swift test` does **not** prove Pencil drawing, photo/PDF import, or session revoke against a live backend.

## XcodeGen

```sh
cd ios
xcodegen generate
open PersonalizedNotes.xcodeproj
```

The generated app is iPad-only (device family 2), iOS 17, Swift 6, linked with SwiftUI and PencilKit. Camera permission is not requested. `NSPhotoLibraryUsageDescription` is present for explicit photo import. `UIBackgroundModes` includes `fetch` and `processing` for later background sync; those modes are declared, not fully scheduled yet.

## Local-first behavior

- Library create / archive / trash writes SQLite immediately and enqueues a pending operation.
- `PKCanvasView` commits `PKDrawing` bytes to Application Support asynchronously on `drawingDidChange`. That path never waits on the network.
- Settings default to Wi-Fi-only uploads. Cellular is skipped; drawing still works offline.
- Training consent in Settings is an explanation plus an acknowledgement flag. It does not silently enroll crops for training.

## Default backend

The iPad client talks to `http://127.0.0.1:8000` unless `personalized-notes.backend-url` is set in UserDefaults. Point that at a reachable host when running on a physical device; loopback is only useful in Simulator next to a local FastAPI process.
