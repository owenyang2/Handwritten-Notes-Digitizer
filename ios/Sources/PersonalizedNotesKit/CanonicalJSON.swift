import Foundation
import CryptoKit

enum CanonicalJSON {
    /// UTF-8 JSON matching Python ``json.dumps(..., separators=(',', ':'), sort_keys=True)``.
    static func data(from value: Any) throws -> Data {
        let text = try stringify(value)
        guard let data = text.data(using: .utf8) else {
            throw InkCodecError.integrityFailure
        }
        return data
    }

    static func stringify(_ value: Any) throws -> String {
        if value is NSNull {
            return "null"
        }
        if let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() {
            return number.boolValue ? "true" : "false"
        }
        if let flag = value as? Bool {
            return flag ? "true" : "false"
        }
        if let number = value as? Int {
            return String(number)
        }
        if let number = value as? Double {
            return encodeFloat(number)
        }
        if let number = value as? NSNumber {
            return encodeFloat(number.doubleValue)
        }
        if let text = value as? String {
            return encodeString(text)
        }
        if let array = value as? [Any] {
            let parts = try array.map { try stringify($0) }
            return "[\(parts.joined(separator: ","))]"
        }
        if let object = value as? [String: Any] {
            let keys = object.keys.sorted()
            let parts = try keys.map { key in
                guard let raw = object[key] else {
                    return "\(encodeString(key)):null"
                }
                return "\(encodeString(key)):\(try stringify(raw))"
            }
            return "{\(parts.joined(separator: ","))}"
        }
        throw InkCodecError.integrityFailure
    }

    private static func encodeString(_ value: String) -> String {
        var encoded = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 0x22: encoded += "\\\""
            case 0x5C: encoded += "\\\\"
            case 0x08: encoded += "\\b"
            case 0x0C: encoded += "\\f"
            case 0x0A: encoded += "\\n"
            case 0x0D: encoded += "\\r"
            case 0x09: encoded += "\\t"
            case 0x00..<0x20:
                encoded += String(format: "\\u%04x", scalar.value)
            default:
                encoded.unicodeScalars.append(scalar)
            }
        }
        encoded += "\""
        return encoded
    }

    private static func encodeFloat(_ value: Double) -> String {
        if value == 0 {
            return value.sign == .minus ? "-0.0" : "0.0"
        }
        if value.rounded() == value, abs(value) < 1e15 {
            return String(format: "%.1f", value)
        }
        var text = String(value)
        if !text.contains("."), !text.contains("e"), !text.contains("E") {
            text += ".0"
        }
        return text
    }
}
