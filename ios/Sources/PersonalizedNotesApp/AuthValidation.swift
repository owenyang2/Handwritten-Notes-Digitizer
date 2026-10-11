import Foundation

enum AuthValidation {
    static func normalized(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func validUsername(_ value: String) -> Bool {
        let username = normalized(value)
        guard (3...64).contains(username.count) else { return false }
        return username.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" || $0 == "." }
    }

    static func validEmail(_ value: String) -> Bool {
        let email = normalized(value)
        guard email.count >= 3, email.count <= 320 else { return false }
        guard let at = email.firstIndex(of: "@"),
              at > email.startIndex,
              at < email.index(before: email.endIndex) else {
            return false
        }
        let domain = email[email.index(after: at)...]
        return domain.contains(".")
            && !email.contains(" ")
            && !email.contains("..")
    }

    static func validPassword(_ value: String) -> Bool {
        value.count >= 8 && value.count <= 128
    }

    static func matchingPasswords(_ password: String, _ confirmation: String) -> Bool {
        validPassword(password) && password == confirmation
    }

    static func validResetToken(_ value: String) -> Bool {
        normalized(value).count >= 16
    }
}
