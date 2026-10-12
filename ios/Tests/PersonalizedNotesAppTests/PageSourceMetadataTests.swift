import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

final class PageSourceMetadataTests: XCTestCase {
    func testPageSpecificSourcesSurviveMixedNotebookLookup() {
        let firstPage = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        let secondPage = PageSummary(
            pageID: UUID(),
            pageIndex: 1,
            width: 612,
            height: 792,
            dpi: 72,
            coordinateSpace: "pdf-points"
        )
        let pdfURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("lecture.pdf")
        let imageURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("diagram.png")
        let metadata: JSONDocument = [
            PageSourceMetadata.key: PageSourceMetadata.merge(
                PageSourceMetadata.entries(
                    sourceURL: imageURL,
                    sourceKind: "image_import",
                    pages: [firstPage],
                    originalFilename: "diagram.png"
                ),
                with: PageSourceMetadata.entries(
                    sourceURL: pdfURL,
                    sourceKind: "pdf_import",
                    pages: [secondPage],
                    originalFilename: "lecture.pdf"
                )
            ),
        ]
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Mixed notebook",
            sourceKind: "native_ink",
            pages: [firstPage, secondPage],
            sourceMetadata: metadata
        )

        let first = PageSourceMetadata.descriptor(for: document, page: firstPage)
        let second = PageSourceMetadata.descriptor(for: document, page: secondPage)

        XCTAssertEqual(first?.url, imageURL)
        XCTAssertEqual(first?.kind, "image_import")
        XCTAssertEqual(first?.sourcePageIndex, 0)
        XCTAssertEqual(second?.url, pdfURL)
        XCTAssertEqual(second?.kind, "pdf_import")
        XCTAssertEqual(second?.sourcePageIndex, 1)
    }

    func testLegacyDocumentLevelSourceRemainsCompatible() {
        let page = PageSummary(
            pageID: UUID(),
            pageIndex: 2,
            width: 612,
            height: 792,
            dpi: 72,
            coordinateSpace: "pdf-points"
        )
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("legacy.pdf")
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Legacy import",
            sourceKind: "pdf_import",
            pages: [page],
            sourceMetadata: ["durable_file_path": .string(url.path)]
        )

        let descriptor = PageSourceMetadata.descriptor(for: document, page: page)

        XCTAssertEqual(descriptor?.url, url)
        XCTAssertEqual(descriptor?.kind, "pdf_import")
        XCTAssertEqual(descriptor?.sourcePageIndex, page.pageIndex)
    }
}
