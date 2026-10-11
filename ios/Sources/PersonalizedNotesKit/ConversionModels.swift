import Foundation

public struct ConversionBlock: Codable, Equatable, Sendable, Identifiable {
    public let regionID: UUID
    public let predictionID: UUID?
    public let bbox: [Int]
    public let contentType: String
    public let orderIndex: Int
    public let predictionText: String?
    public let reviewedText: String?
    public let excluded: Bool
    public let reviewVersion: Int?
    public let truncated: Bool
    public let engineID: String?
    public let sourceStale: Bool?
    public let cropRevision: Int?
    public var id: UUID { regionID }
}

public struct SavedRegionReview: Codable, Equatable, Sendable {
    public let regionID: UUID
    public let version: Int
    public let documentRevisionID: UUID?
}

public struct ConvertedDocumentExport: Codable, Equatable, Sendable {
    public let status: String
    public let format: String
    public let text: String
}
