# Handwriting Notes Digitizer — native client

The SwiftUI/PencilKit client provides notebooks, offline local storage, page ink,
PDF/image import, synchronization, source-linked conversion review, and export.
Its current app and Xcode target name is **Personalized Notes**.

This is a **local alpha** with a universal iPhone/iPad target. Real-model
simulator workflows and later native regression suites are recorded in
[status and roadmap](../docs/STATUS.md). Physical Apple Pencil/device acceptance
remains outstanding.

## Source map

| Path | Responsibility |
| --- | --- |
| `Sources/PersonalizedNotesKit/SQLiteLocalStore.swift` | Durable local documents, revisions, pending operations, and transfers |
| `Sources/PersonalizedNotesKit/SyncEngine.swift` | Pending-operation sync/retry and remote projection |
| `Sources/PersonalizedNotesKit/InkV1.swift`, `CanonicalJSON.swift` | Canonical ink data and hashing boundary |
| `Sources/PersonalizedNotesKit/SharedBackendClient.swift`, `APIModels.swift` | HTTP/DTO boundary; shared timestamps/errors |
| `Sources/PersonalizedNotesKit/Session.swift` | Session vault |
| `Sources/PersonalizedNotesApp/AppModel.swift` | Local library, sync, exact ink restoration, and conversion orchestration |
| `Sources/PersonalizedNotesApp/NotebookEditorView.swift`, `PencilCanvasRepresentable.swift` | Notebook pages and PencilKit canvas |
| `Sources/PersonalizedNotesApp/ConversionReviewView.swift`, `LocalExport.swift` | Source-linked review and original/converted exports |
| `Sources/PersonalizedNotesApp/ImportCoordinator.swift`, `NativeArchiveImporter.swift` | Source import and native archives |
| `Sources/ShareExtension/` | Share-sheet import into the App Group inbox |
| `Tests/`, `UITests/` | Shared-kit, native runtime, and simulator UI tests |
| `project.yml`, `Package.swift` | XcodeGen app/extension spec and Swift package |

## Run in Simulator

Requires macOS, Xcode, and XcodeGen. From the repository root:

```bash
cd ios
xcodegen generate
open PersonalizedNotes.xcodeproj
```

Choose the `PersonalizedNotes` scheme and an iPad simulator. Keep local ad-hoc
signing enabled: the recorded setup found that disabling signing prevented
normal Keychain use. No Apple developer account was needed for that simulator
setup. The app supports iPhone and iPad, targets iOS 17+, and uses Swift 6.

Run the API using [getting started](../docs/guides/getting-started.md), and install
models using the [recorded macOS setup](../docs/guides/recognition-and-ipad.md) for
automatic conversion. The simulator's default backend is
`http://127.0.0.1:8000`. Choose **Continue Offline** for local notebook creation;
sign in to the local backend to sync.

## Physical-device setup

Follow the [physical iPad instructions](../docs/guides/recognition-and-ipad.md#your-physical-ipad)
for the signing team, reachable backend host, and App Group. Current identifiers:

- App: `com.handwrittennotes.digitizer`
- Share extension: `com.handwrittennotes.digitizer.share`
- Shared App Group: `group.com.handwrittennotes.digitizer`

Provision the App Group for both targets before physical share-import testing.
Without its container, the host app can fall back to Application Support for
kit-side ingest, while the extension fails closed. Debug builds support a
controlled local HTTP host; non-loopback release endpoints require HTTPS.

Physical testing still needs to establish Pencil/palm rejection, finger
pan/zoom, signing/App Groups, actual share extension behavior, accessibility,
rotation/multitasking, and battery/thermal behavior.

## Local behavior and limits

- Notebook/page edits write to local SQLite and enqueue pending operations.
- PencilKit snapshots persist locally without waiting for the network. Remote
  ink installation rechecks pending local revisions so in-flight downloads
  cannot replace a newly edited drawing.
- Settings default to Wi-Fi-only uploads; local drawing remains available
  offline.
- Share import stages files in the App Group inbox; the extension does not
  upload directly.
- Training consent remains explicit. Settings acknowledgement does not silently
  enroll crops in a dataset.
- Background modes are declared, but complete long-running background-transfer
  scheduling and acceptance remain outstanding.

## Tests and evidence

```bash
swift test --filter PersonalizedNotesKitTests
```

That command covers the shared kit's ink, local store, and sync logic on macOS.
It does not run the UIKit/PencilKit app or its UI tests. Run the native app and
UI test targets in the generated Xcode project for simulator coverage. The
real-model `LiveConversionDemoUITests` journey is opt-in and requires models
and disposable demo credentials; keep raw sign-in logs out of Git.

See [current status and evidence](../docs/STATUS.md) for the latest recorded
results and open acceptance work. The final October 14 report records app and
UI suites passing on iPhone and iPad simulators, including the compact Tools
menu. These checks do not establish physical-device readiness.

## Appearance and adaptive review

Settings persists System / Light / Dark appearance. Conversion review adapts
to available width and guards unsaved dismissal. PencilKit and local ink
persistence keep their existing contracts. Use
[the design reference](../docs/reference/design.md) for shared UI roles and labels.
Compact toolbars can move actions into the system More menu; simulator UI tests
must open that menu and scroll action lists before selecting an item.

[Project overview](../README.md) · [Documentation index](../docs/README.md)
