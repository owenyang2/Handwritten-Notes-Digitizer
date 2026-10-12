import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

final class PageNavigatorFilterTests: XCTestCase {
    func testFilterMatchesNumberedPagesAndDiacriticInsensitiveTitles() {
        let numberedPage = PageSummary(
            pageID: UUID(),
            pageIndex: 4,
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        let titledPage = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            title: "Résumé",
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )

        XCTAssertTrue(PageNavigatorFilter.matches(numberedPage, query: "page 5"))
        XCTAssertTrue(PageNavigatorFilter.matches(numberedPage, query: "5"))
        XCTAssertTrue(PageNavigatorFilter.matches(titledPage, query: "resume"))
        XCTAssertFalse(PageNavigatorFilter.matches(numberedPage, query: "introduction"))
    }
}
