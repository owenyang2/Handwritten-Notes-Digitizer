import XCTest
import PDFKit
import UIKit

@testable import PersonalizedNotes

final class ImportSourcePreflightTests: XCTestCase {
    func testMislabeledPDFFailsBeforeDocumentCreation() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-not-a-pdf-\(UUID().uuidString).pdf")
        try Data("this is not a PDF".utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        XCTAssertThrowsError(
            try ImportedSourceFactory.makeDocument(sourceURL: url, originalFilename: "notes.pdf")
        ) { error in
            guard case ImportedSourceError.unreadable = error else {
                return XCTFail("Expected unreadable, got \(error)")
            }
        }
    }

    func testMislabeledImageFailsBeforeRasterDecode() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-not-an-image-\(UUID().uuidString).png")
        try Data("not an image".utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        XCTAssertThrowsError(
            try ImportedSourceFactory.makeDocument(sourceURL: url, originalFilename: "notes.png")
        ) { error in
            guard case ImportedSourceError.unsupported = error else {
                return XCTFail("Expected unsupported, got \(error)")
            }
        }
    }

    func testValidPDFAndPNGPassStructuralPreflight() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-preflight-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let imageURL = directory.appendingPathComponent("diagram.png")
        let image = UIGraphicsImageRenderer(size: CGSize(width: 32, height: 24)).image { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 32, height: 24))
        }
        try XCTUnwrap(image.pngData()).write(to: imageURL)

        let pdfURL = directory.appendingPathComponent("lecture.pdf")
        let pdfRenderer = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 200, height: 300))
        try pdfRenderer.writePDF(to: pdfURL) { context in
            context.beginPage()
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 200, height: 300))
        }

        let imageDocument = try ImportedSourceFactory.makeDocument(
            sourceURL: imageURL,
            originalFilename: "diagram.png"
        )
        XCTAssertEqual(imageDocument.pages.first?.width, 64)
        XCTAssertEqual(imageDocument.pages.first?.height, 48)

        let pdfDocument = try ImportedSourceFactory.makeDocument(
            sourceURL: pdfURL,
            originalFilename: "lecture.pdf"
        )
        XCTAssertEqual(pdfDocument.pages.count, 1)
        XCTAssertEqual(pdfDocument.pages.first?.width, 200)
        XCTAssertEqual(pdfDocument.pages.first?.height, 300)
    }
}
