import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

#if canImport(PencilKit)
import PencilKit
#endif

final class LocalInkCommitRecoveryTests: XCTestCase {
    func testRemoteDownloadCannotReplaceALocalCommitThatArrivesInFlight() async throws {
        for initiallyEmpty in [false, true] {
            let pageID = UUID()
            defer { LocalInkCache.remove(pageID: pageID) }
            let queue = InkCommitQueue.shared
            let oldBundle = makeBundle(pageID: pageID, marker: "old")
            if !initiallyEmpty {
                _ = try await queue.commit(pageID: pageID, pkDrawingData: oldBundle.nativeDrawing!.data, bundle: oldBundle)
                try await queue.markInkUploaded(pageID: pageID, contentHash: oldBundle.contentHash)
            }
            let expectedID = LocalInkCache.revisionMetadata(pageID: pageID)?.revisionID
            let expectedHash = LocalInkCache.readInkBundle(pageID: pageID)?.contentHash
            let remote = makeRestorableBundle(pageID: pageID)
            let revision = makeRevision(remote)
            // The network download is in flight while Pencil publishes new ink.
            let newBundle = makeBundle(pageID: pageID, marker: "new")
            let local = try await queue.commit(pageID: pageID, pkDrawingData: newBundle.nativeDrawing!.data, bundle: newBundle)
            let installed = try await queue.installRemoteBundle(
                remote, revision: revision, expectedLocalRevisionID: expectedID, expectedLocalContentHash: expectedHash
            )
            XCTAssertNil(installed)
            XCTAssertEqual(LocalInkCache.revisionMetadata(pageID: pageID)?.revisionID, local.revisionID)
            XCTAssertEqual(LocalInkCache.readInkBundle(pageID: pageID), newBundle)
            try await queue.markInkUploaded(pageID: pageID, contentHash: oldBundle.contentHash)
            XCTAssertFalse(LocalInkCache.isInkUploaded(pageID: pageID, contentHash: newBundle.contentHash))
        }
    }

    func testQueuedRemoteInstallRestoresAnUnchangedUploadedRevision() async throws {
        let pageID = UUID()
        defer { LocalInkCache.remove(pageID: pageID) }
        let old = makeBundle(pageID: pageID, marker: "old")
        let metadata = try await InkCommitQueue.shared.commit(pageID: pageID, pkDrawingData: old.nativeDrawing!.data, bundle: old)
        try await InkCommitQueue.shared.markInkUploaded(pageID: pageID, contentHash: old.contentHash)
        let remote = makeRestorableBundle(pageID: pageID)
        let revision = makeRevision(remote)
        let installed = try await InkCommitQueue.shared.installRemoteBundle(
            remote, revision: revision, expectedLocalRevisionID: metadata.revisionID, expectedLocalContentHash: old.contentHash
        )
        XCTAssertEqual(installed?.revisionID, revision.bundleID)
        XCTAssertEqual(LocalInkCache.readPKDrawing(pageID: pageID), remote.nativeDrawing?.data)
    }

    private func makeRevision(_ bundle: InkBundle) -> InkBundleRevision {
        InkBundleRevision(
            bundleID: UUID(), pageID: bundle.pageID, contentHash: bundle.contentHash,
            schemaName: bundle.schema, schemaVersion: bundle.schemaVersion,
            strokeCount: bundle.strokes.count, pointCount: 2, assetID: UUID(),
            createdAt: Date(), revisionNumber: 2, active: true, uploadState: "complete"
        )
    }

    func testRemoteRevisionRestoresExactNativePairWithoutOverwritingPendingLocalInk() throws {
        let pageID = UUID()
        let localBundle = makeBundle(pageID: pageID, marker: "old-local")
        let remoteBundle = makeRestorableBundle(pageID: pageID)
        let remoteRevision = InkBundleRevision(
            bundleID: UUID(),
            pageID: pageID,
            contentHash: remoteBundle.contentHash,
            schemaName: remoteBundle.schema,
            schemaVersion: remoteBundle.schemaVersion,
            strokeCount: remoteBundle.strokes.count,
            pointCount: remoteBundle.strokes.reduce(0) { $0 + $1.points.count },
            assetID: UUID(),
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            revisionNumber: 2,
            parentBundleID: UUID(),
            active: true,
            uploadState: "complete"
        )
        defer { LocalInkCache.remove(pageID: pageID) }

        _ = try LocalInkCache.commit(
            pageID: pageID,
            pkDrawingData: try XCTUnwrap(localBundle.nativeDrawing?.data),
            bundle: localBundle
        )
        XCTAssertFalse(LocalInkCache.shouldRestoreRemoteRevision(
            pageID: pageID,
            localBundle: localBundle,
            activeRevision: remoteRevision
        ))

        try LocalInkCache.markInkUploaded(pageID: pageID, contentHash: localBundle.contentHash)
        XCTAssertTrue(LocalInkCache.shouldRestoreRemoteRevision(
            pageID: pageID,
            localBundle: localBundle,
            activeRevision: remoteRevision
        ))

        let installed = try LocalInkCache.installRemoteBundle(
            remoteBundle,
            revision: remoteRevision
        )
        XCTAssertEqual(installed.revisionID, remoteRevision.bundleID)
        XCTAssertEqual(LocalInkCache.readInkBundle(pageID: pageID), remoteBundle)
        XCTAssertEqual(
            LocalInkCache.readPKDrawing(pageID: pageID),
            remoteBundle.nativeDrawing?.data
        )
        XCTAssertTrue(LocalInkCache.isInkUploaded(
            pageID: pageID,
            contentHash: remoteBundle.contentHash
        ))
    }

    func testEveryInjectedCommitBoundaryReopensOnlyACompleteRevision() throws {
        let pageID = UUID()
        let oldBundle = makeBundle(pageID: pageID, marker: "old")
        let newBundle = makeBundle(pageID: pageID, marker: "new")
        let oldDrawing = try XCTUnwrap(oldBundle.nativeDrawing?.data)
        let newDrawing = try XCTUnwrap(newBundle.nativeDrawing?.data)
        defer {
            LocalInkCache.setTestCommitFailurePoint(nil)
            LocalInkCache.remove(pageID: pageID)
        }

        for failurePoint in LocalInkCache.CommitFailurePoint.allCases {
            let committedMetadata = try LocalInkCache.commit(
                pageID: pageID,
                pkDrawingData: oldDrawing,
                bundle: oldBundle
            )
            let reopenedMetadata = try XCTUnwrap(LocalInkCache.revisionMetadata(pageID: pageID))
            XCTAssertEqual(reopenedMetadata.revisionID, committedMetadata.revisionID)
            XCTAssertEqual(reopenedMetadata.pkDrawingPath, committedMetadata.pkDrawingPath)
            XCTAssertEqual(reopenedMetadata.inkBundlePath, committedMetadata.inkBundlePath)
            XCTAssertTrue(FileManager.default.fileExists(atPath: reopenedMetadata.pkDrawingPath))
            XCTAssertTrue(FileManager.default.fileExists(atPath: reopenedMetadata.inkBundlePath))
            LocalInkCache.setTestCommitFailurePoint(failurePoint)

            XCTAssertThrowsError(
                try LocalInkCache.commit(
                    pageID: pageID,
                    pkDrawingData: newDrawing,
                    bundle: newBundle
                )
            ) { error in
                guard let failure = error as? LocalInkCommitTestFailure else {
                    return XCTFail("Expected injected failure, got \(error)")
                }
                XCTAssertEqual(failure.point, failurePoint)
            }
            LocalInkCache.setTestCommitFailurePoint(nil)

            let reopenedDrawing = try XCTUnwrap(LocalInkCache.readPKDrawing(pageID: pageID))
            let reopenedBundle = try XCTUnwrap(LocalInkCache.readInkBundle(pageID: pageID))
            if failurePoint == .afterWritingManifest {
                XCTAssertEqual(reopenedDrawing, newDrawing)
                XCTAssertEqual(reopenedBundle, newBundle)
            } else {
                XCTAssertEqual(reopenedDrawing, oldDrawing)
                XCTAssertEqual(reopenedBundle, oldBundle)
            }
            XCTAssertEqual(reopenedBundle.nativeDrawing?.data, reopenedDrawing)
        }
    }

    private func makeBundle(pageID: UUID, marker: String) -> InkBundle {
        let data = Data(marker.utf8)
        let strokeID = marker.hasPrefix("new")
            ? UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
            : UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        return InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 768, height: 1024),
            strokes: [
                InkStroke(
                    strokeID: strokeID,
                    points: [
                        InkPoint(x: 10, y: 10, timeOffset: 0),
                        InkPoint(x: Double(marker.count * 10), y: 40, timeOffset: 0.1),
                    ]
                ),
            ],
            nativeDrawing: NativeDrawing(data: data)
        )
    }

    private func makeRestorableBundle(pageID: UUID) -> InkBundle {
        #if canImport(PencilKit)
        let nativeData = PKDrawing().dataRepresentation()
        #else
        let nativeData = Data("valid-native-drawing".utf8)
        #endif
        return InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 768, height: 1024),
            strokes: [
                InkStroke(
                    points: [
                        InkPoint(x: 10, y: 10, timeOffset: 0),
                        InkPoint(x: 40, y: 40, timeOffset: 0.1),
                    ]
                )
            ],
            nativeDrawing: NativeDrawing(data: nativeData)
        )
    }
}
