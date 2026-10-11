import XCTest

@testable import PersonalizedNotes

final class AuthValidationTests: XCTestCase {
    func testUsernameValidationAcceptsSafeIdentifiersAndRejectsAmbiguousInput() {
        XCTAssertTrue(AuthValidation.validUsername("student_01"))
        XCTAssertTrue(AuthValidation.validUsername("a.b-c"))
        XCTAssertFalse(AuthValidation.validUsername("ab"))
        XCTAssertFalse(AuthValidation.validUsername("student name"))
        XCTAssertFalse(AuthValidation.validUsername("student/01"))
    }

    func testEmailValidationRejectsWhitespaceAndMalformedDomains() {
        XCTAssertTrue(AuthValidation.validEmail("person@example.edu"))
        XCTAssertFalse(AuthValidation.validEmail("person example.edu"))
        XCTAssertFalse(AuthValidation.validEmail("person@example"))
        XCTAssertFalse(AuthValidation.validEmail("@example.edu"))
        XCTAssertFalse(AuthValidation.validEmail("person..name@example.edu"))
    }

    func testPasswordAndResetTokenValidation() {
        XCTAssertTrue(AuthValidation.matchingPasswords("correct horse", "correct horse"))
        XCTAssertFalse(AuthValidation.matchingPasswords("short", "short"))
        XCTAssertFalse(AuthValidation.matchingPasswords("correct horse", "different"))
        XCTAssertTrue(AuthValidation.validResetToken(String(repeating: "a", count: 16)))
        XCTAssertFalse(AuthValidation.validResetToken("too-short"))
    }
}
