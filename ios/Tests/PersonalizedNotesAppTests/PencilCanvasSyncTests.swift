import XCTest
import PencilKit
import UIKit
@testable import PersonalizedNotes

@MainActor
final class PencilCanvasSyncTests: XCTestCase {
    func testSamePageRemoteRevisionUpdatesCanvasAndCleanNavigationDoesNotCommit() throws {
        let pageID = UUID()
        var commits: [Data] = []
        let coordinator = PencilCanvasRepresentable.Coordinator(pageID: pageID) { _, data in commits.append(data) }
        let canvas = PKCanvasView()
        let old = drawing(x: 10).dataRepresentation()
        let remote = drawing(x: 30).dataRepresentation()
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: old, revisionID: UUID())
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: remote, revisionID: UUID())
        XCTAssertEqual(canvas.drawing, try PKDrawing(data: remote))
        // PencilKit may notify its delegate after an externally assigned drawing.
        coordinator.canvasViewDrawingDidChange(canvas)
        coordinator.flush(canvas)
        coordinator.updateDrawing(on: canvas, pageID: UUID(), data: nil, revisionID: nil)
        XCTAssertTrue(commits.isEmpty)
    }

    func testRemoteRevisionPreservesPendingAndSubmittedPencilEditsUntilLocalSaveIsAcknowledged() throws {
        let pageID = UUID()
        var commits: [Data] = []
        let coordinator = PencilCanvasRepresentable.Coordinator(pageID: pageID) { _, data in commits.append(data) }
        let canvas = PKCanvasView()
        let old = drawing(x: 10).dataRepresentation()
        let remote = drawing(x: 30).dataRepresentation()
        let local = drawing(x: 50)
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: old, revisionID: UUID())
        canvas.drawing = local
        coordinator.canvasViewDrawingDidChange(canvas)
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: remote, revisionID: UUID())
        XCTAssertEqual(canvas.drawing.dataRepresentation(), local.dataRepresentation())
        coordinator.flush(canvas)
        XCTAssertEqual(commits, [local.dataRepresentation()])
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: remote, revisionID: UUID())
        XCTAssertEqual(canvas.drawing.dataRepresentation(), local.dataRepresentation())
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: local.dataRepresentation(), revisionID: UUID())
        coordinator.flush(canvas)
        XCTAssertEqual(commits.count, 1)
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: remote, revisionID: UUID())
        XCTAssertEqual(canvas.drawing, try PKDrawing(data: remote))
    }

    func testUndoBeforeDebouncedCommitPublishesTheLatestDrawing() {
        let pageID = UUID()
        var commits: [Data] = []
        let coordinator = PencilCanvasRepresentable.Coordinator(pageID: pageID) { _, data in commits.append(data) }
        let canvas = PKCanvasView()
        let old = drawing(x: 10)
        coordinator.updateDrawing(on: canvas, pageID: pageID, data: old.dataRepresentation(), revisionID: UUID())
        canvas.drawing = drawing(x: 50)
        coordinator.canvasViewDrawingDidChange(canvas)
        canvas.drawing = old
        coordinator.canvasViewDrawingDidChange(canvas)
        coordinator.flush(canvas)
        XCTAssertEqual(commits, [old.dataRepresentation()])
    }

    func testDecodedPencilSnapshotCommitsAnExactNativePair() async throws {
        let pageID = UUID()
        defer { LocalInkCache.remove(pageID: pageID) }
        let data = drawing(x: 30).dataRepresentation()
        let decoded = try PKDrawing(data: data)
        let bundle = try PencilKitInkAdapter.makeBundle(
            drawing: decoded, pageID: pageID, width: 120, height: 120, nativeDrawingData: data
        )
        _ = try await InkCommitQueue.shared.commit(pageID: pageID, pkDrawingData: data, bundle: bundle)
        XCTAssertEqual(LocalInkCache.readPKDrawing(pageID: pageID), data)
        XCTAssertEqual(LocalInkCache.readInkBundle(pageID: pageID), bundle)
        XCTAssertEqual(bundle.nativeDrawing?.data, data)
    }

    private func drawing(x: CGFloat) -> PKDrawing {
        let points = [CGPoint(x: x, y: 10), CGPoint(x: x + 20, y: 30)].enumerated().map { index, point in
            PKStrokePoint(location: point, timeOffset: Double(index), size: CGSize(width: 4, height: 4),
                          opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        let stroke = PKStroke(ink: PKInk(.pen, color: .black), path: PKStrokePath(controlPoints: points, creationDate: Date()))
        return PKDrawing(strokes: [stroke])
    }
}
