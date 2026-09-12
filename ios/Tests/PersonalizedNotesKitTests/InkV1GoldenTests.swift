import XCTest
import Foundation
@testable import PersonalizedNotesKit

final class InkV1GoldenTests: XCTestCase {
    func testContentHashIsStableAcrossTwoEncodes() throws {
        let bundle = Self.makeFixedBundle()
        let first = try InkV1Codec.encode(bundle)
        let second = try InkV1Codec.encode(bundle)
        XCTAssertEqual(first, second)

        let again = InkBundle(
            pageID: bundle.pageID,
            coordinateSpace: bundle.coordinateSpace,
            strokes: bundle.strokes,
            capture: bundle.capture,
            nativeDrawing: bundle.nativeDrawing
        )
        XCTAssertEqual(again.contentHash, bundle.contentHash)
        XCTAssertEqual(try InkV1Codec.encode(again), first)
    }

    func testEncodeDecodeRoundTrip() throws {
        let bundle = Self.makeFixedBundle()
        let encoded = try InkV1Codec.encode(bundle)
        if ProcessInfo.processInfo.environment["DUMP_INK_FIXTURE"] == "1" {
            FileHandle.standardError.write(encoded)
            FileHandle.standardError.write(Data("\n".utf8))
        }
        let decoded = try InkV1Codec.decode(encoded)
        XCTAssertEqual(decoded, bundle)
        XCTAssertEqual(decoded.contentHash, bundle.contentHash)
        try decoded.validate()
    }

    func testGoldenFixtureRoundTrip() throws {
        let bundle = Self.makeFixedBundle()
        let fixtureURL = Self.fixtureURL()
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: fixtureURL.path),
            "Missing fixture at \(fixtureURL.path)"
        )
        let data = try Data(contentsOf: fixtureURL)
        let decoded = try InkV1Codec.decode(data)
        XCTAssertEqual(decoded.pageID, bundle.pageID)
        XCTAssertEqual(decoded.strokes, bundle.strokes)
        XCTAssertEqual(decoded.contentHash, bundle.contentHash)
        XCTAssertEqual(decoded, bundle)

        let reencoded = try InkV1Codec.encode(decoded)
        XCTAssertEqual(try InkV1Codec.decode(reencoded), bundle)
    }

    static func makeFixedBundle() -> InkBundle {
        let pageID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let strokeID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        return InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 800, height: 600),
            strokes: [
                InkStroke(
                    strokeID: strokeID,
                    points: [
                        InkPoint(x: 10, y: 20, timeOffset: 0, pressure: 0.5),
                        InkPoint(x: 40, y: 80, timeOffset: 0.25, pressure: 0.75),
                    ],
                    tool: .pen,
                    color: InkRGBA(red: 0, green: 0, blue: 0, alpha: 1),
                    width: 2
                ),
            ],
            capture: InkCaptureCapabilities(
                pressure: true,
                azimuth: false,
                altitude: false,
                timing: true,
                source: "apple_pencil"
            )
        )
    }

    static func fixtureURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures")
            .appendingPathComponent("ink_v1_simple.json")
    }
}
