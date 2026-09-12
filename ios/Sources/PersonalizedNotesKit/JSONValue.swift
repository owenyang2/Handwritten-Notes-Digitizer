import Foundation

/// A small, transport-neutral JSON value used for operation payloads.
///
/// Keeping payloads typed as JSON instead of `[String: Any]` makes the sync
/// log Codable, testable, and safe to persist without depending on a web
/// framework or generated client runtime.
public enum JSONValue: Codable, Equatable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null:
            try container.encodeNil()
        case let .bool(value):
            try container.encode(value)
        case let .number(value):
            try container.encode(value)
        case let .string(value):
            try container.encode(value)
        case let .array(value):
            try container.encode(value)
        case let .object(value):
            try container.encode(value)
        }
    }
}

public typealias JSONDocument = [String: JSONValue]

/// The JSON configuration shared by the API client and durable local store.
public enum APIJSON {
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        // Foundation's convertFromSnakeCase turns `page_id` into `pageId`, which
        // does not match public `pageID` properties. Preserve a trailing `id` /
        // `ids` component as `ID` / `IDs` so kit models round-trip.
        decoder.keyDecodingStrategy = .custom { path in
            AnyJSONCodingKey(stringValue: decodeSnakeCasePreservingID(path.last?.stringValue ?? ""))
        }
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

private struct AnyJSONCodingKey: CodingKey {
    var stringValue: String
    var intValue: Int?

    init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init?(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }
}

private func decodeSnakeCasePreservingID(_ key: String) -> String {
    guard key.contains("_") else { return key }
    let parts = key.split(separator: "_", omittingEmptySubsequences: true).map { $0.lowercased() }
    guard let first = parts.first else { return key }
    let rest = parts.dropFirst().map { part -> String in
        if part == "id" { return "ID" }
        if part == "ids" { return "IDs" }
        return part.prefix(1).uppercased() + part.dropFirst()
    }.joined()
    return first + rest
}
