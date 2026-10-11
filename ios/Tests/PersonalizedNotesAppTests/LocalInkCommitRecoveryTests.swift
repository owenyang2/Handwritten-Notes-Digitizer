import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

#if canImport(PencilKit)
import PencilKit
#endif

final class LocalInkCommitRecoveryTests: XCTestCase {
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
