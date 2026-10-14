# Handwriting Notes Digitizer — iPad client

The SwiftUI/PencilKit client provides notebooks, offline local storage, page ink,
PDF/image import, synchronization, source-linked conversion review, and export.
Its current app and Xcode target name is **Personalized Notes**.

This is an **iPad alpha**. A signed simulator app and real-model backend journey
were exercised on October 11, 2026; physical Apple Pencil/device acceptance
remains outstanding. See the [demo](../demos/README.md),
[feature matrix](../docs/FEATURE_STATUS_MATRIX.md), and
[remaining work](../TODO-2026-10-11.md).

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
setup. The app is iPad-only, targets iOS 17+, and uses Swift 6.

Run the API using [getting started](../docs/GETTING_STARTED.md), and install
models using the [recorded macOS setup](../docs/MODEL_AND_IPAD_SETUP.md) for
automatic conversion. The simulator's default backend is
`http://127.0.0.1:8000`. Choose **Continue Offline** for local notebook creation;
sign in to the local backend to sync.

## Physical-device setup

Follow the [physical iPad instructions](../docs/MODEL_AND_IPAD_SETUP.md#your-physical-ipad)
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

The [October 11 regression report](../docs/release-evidence/REVIEW_FIXES_2026-10-11.md)
records 30 shared-kit and 33 native app tests passing. The
[local acceptance report](../docs/release-evidence/END_TO_END_LOCAL_2026-10-11.md)
records the simulator recognition → review → save/reopen → Markdown-sharing
journey. Broader two-client interruption/conflict and physical testing remain.

[Project overview](../README.md) · [Documentation index](../docs/README.md)

## Quiet Notebook UI alignment (October 14, 2026)

The generated target now supports iPhone and iPad. Settings persists System / Light / Dark appearance; conversion review adapts to available width and guards unsaved dismissal. PencilKit and local ink persistence retain their existing contracts. Universal compilation is not a claim of physical iPhone or Pencil readiness. See the [UI revamp evidence](../docs/release-evidence/UI_REVAMP_2026-10-14.md) for simulator results, screenshots, and remaining device gates.

The October 14 follow-up also checks the largest accessibility Dynamic Type
size on iPhone, including scrolling to Continue Offline, changing appearance,
and creating a notebook with the keyboard open. The appearance picker uses a
full row to avoid splitting its label/value into narrow columns. This targeted
reachability check does not replace a complete Dynamic Type or VoiceOver audit.
Compact native toolbars can move actions into the system More menu; UI tests
must open that menu and scroll long action lists before selecting an item.
The initial expanded iPhone run exposed a Tools-menu failure at Toggle Ruler;
the final verification below resolves it and records complete green suites.

The [final October 14 verification](../docs/release-evidence/VERIFICATION_AND_PONYTAIL_AUDIT_2026-10-14.md)
records 33 native app tests and all eight UI tests passing on both iPhone and
iPad simulators. UI-test launch mode disables UIKit animations to keep those
functional checks deterministic; production retains normal animations. The
earlier compact Tools-menu failure is resolved, and page-deletion confirmation
retains its target correctly. Physical-device and broader accessibility gates remain.
