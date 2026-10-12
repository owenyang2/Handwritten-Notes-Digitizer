import XCTest

@testable import PersonalizedNotes
import ShareInbox

final class ShareImportDurabilityTests: XCTestCase {
    func testShareSourceIsDurableBeforeInboxAcknowledgementAndRestagingIsIdempotent() throws {
        let filename = "lecture-\(UUID().uuidString).pdf"
        let sourceData = Data("durable share source".utf8)
        let item = try ShareInboxStore.writePendingImport(
            data: sourceData,
            filename: filename,
            uniformTypeIdentifier: "com.adobe.pdf",
            allowFallback: true
        )
        defer { try? ShareInboxStore.markConsumed(item, allowFallback: true) }

        let firstStage = try ImportCoordinator.stageShare(item)
        XCTAssertTrue(FileManager.default.fileExists(atPath: firstStage.sourceURL.path))
        XCTAssertEqual(try Data(contentsOf: firstStage.sourceURL), sourceData)
        XCTAssertEqual(try ShareInboxStore.listPending(allowFallback: true).filter { $0.id == item.id }.count, 1)

        let secondStage = try ImportCoordinator.stageShare(item)
        XCTAssertEqual(secondStage.sourceURL, firstStage.sourceURL)
        XCTAssertEqual(try Data(contentsOf: secondStage.sourceURL), sourceData)

        // A process termination at this point leaves the app-owned copy
        // recoverable even though the share inbox has not been acknowledged.
        try ShareInboxStore.markConsumed(item, allowFallback: true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try ShareInboxStore.fileURL(for: item, allowFallback: true).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: firstStage.sourceURL.path))

        try DurableImportStore.removeOwnedFile(at: firstStage.sourceURL.path)
        XCTAssertFalse(FileManager.default.fileExists(atPath: firstStage.sourceURL.path))
    }

    func testMissingShareFileLeavesInboxManifestRetryable() throws {
        let item = try ShareInboxStore.writePendingImport(
            data: Data("manifest survives".utf8),
            filename: "missing-\(UUID().uuidString).pdf",
            uniformTypeIdentifier: "com.adobe.pdf",
            allowFallback: true
        )
        defer { try? ShareInboxStore.markConsumed(item, allowFallback: true) }
        let inboxFile = try ShareInboxStore.fileURL(for: item, allowFallback: true)
        try FileManager.default.removeItem(at: inboxFile)

        XCTAssertThrowsError(try ImportCoordinator.stageShare(item))
        XCTAssertTrue(
            try ShareInboxStore.listPending(allowFallback: true).contains { $0.id == item.id },
            "A failed copy must not acknowledge or discard the inbox manifest."
        )
    }

    func testCleanupCannotDeleteAPathOutsideAppOwnedImports() throws {
        let outsideURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-outside-import-\(UUID().uuidString)")
        try Data("must remain".utf8).write(to: outsideURL)
        defer { try? FileManager.default.removeItem(at: outsideURL) }

        try DurableImportStore.removeOwnedFile(at: outsideURL.path)

        XCTAssertTrue(FileManager.default.fileExists(atPath: outsideURL.path))
    }
}
