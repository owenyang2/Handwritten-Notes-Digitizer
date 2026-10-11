import XCTest
@testable import PersonalizedNotesKit

final class BackendTimestampTests: XCTestCase {
    func testSQLiteAndPostgresTimestampsDecodeAsUTC() throws {
        struct Row: Decodable { let createdAt: Date }
        let timestamps = ["2026-10-11T21:08:54.249401", "2026-10-11T21:08:54.249401+00:00", "2026-10-11T21:08:54.249Z"]
        let rows = try timestamps.map { value in
            try APIJSON.decoder().decode(Row.self, from: Data("{\"created_at\":\"\(value)\"}".utf8))
        }
        XCTAssertEqual(rows[0].createdAt.timeIntervalSince1970, rows[1].createdAt.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertEqual(rows[0].createdAt.timeIntervalSince1970, rows[2].createdAt.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertNoThrow(try APIJSON.decoder().decode(Row.self, from: Data("{\"created_at\":\"2026-10-11T21:08:54Z\"}".utf8)))
        XCTAssertThrowsError(try APIJSON.decoder().decode(Row.self, from: Data("{\"created_at\":\"invalid\"}".utf8)))
    }
}
