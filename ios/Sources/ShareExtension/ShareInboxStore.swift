import Foundation
import UniformTypeIdentifiers

/// App Group used by the iPad app and the share extension.
/// Replace this placeholder identifier in Apple Developer / Xcode signing
/// before shipping; physical-device tests require a matching App Group.
public enum ShareInboxConfiguration {
    public static let appGroupIdentifier = "group.com.handwrittennotes.digitizer"
    public static let pendingDirectoryName = "PendingImports"
    public static let filesDirectoryName = "files"
    public static let manifestsDirectoryName = "manifests"
}

public enum ShareInboxError: Error, Equatable, Sendable {
    case appGroupUnavailable
    case invalidPayload
}

public struct PendingShareImport: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let originalFilename: String
    public let uniformTypeIdentifier: String
    public let relativeFilePath: String
    public let createdAt: Date

    public init(
        id: UUID = UUID(),
        originalFilename: String,
        uniformTypeIdentifier: String,
        relativeFilePath: String,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.originalFilename = originalFilename
        self.uniformTypeIdentifier = uniformTypeIdentifier
        self.relativeFilePath = relativeFilePath
        self.createdAt = createdAt
    }

    public var isPDF: Bool {
        uniformTypeIdentifier == UTType.pdf.identifier
            || uniformTypeIdentifier == "com.adobe.pdf"
    }
}

/// Writes PDF/image payloads into the App Group container so the host app can
/// import them without blocking the share sheet on network I/O.
public enum ShareInboxStore {
    public static func containerURL(allowFallback: Bool) -> URL? {
        if let group = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: ShareInboxConfiguration.appGroupIdentifier
        ) {
            return group
        }
        guard allowFallback else { return nil }
        let fallback = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PersonalizedNotes/ShareInboxFallback", isDirectory: true)
        return fallback
    }

    public static func writePendingImport(
        data: Data,
        filename: String,
        uniformTypeIdentifier: String,
        allowFallback: Bool = false
    ) throws -> PendingShareImport {
        guard !data.isEmpty else { throw ShareInboxError.invalidPayload }
        let root = try preparedRoot(allowFallback: allowFallback)
        let id = UUID()
        let safeName = sanitizedFilename(filename)
        let relative = "\(ShareInboxConfiguration.filesDirectoryName)/\(id.uuidString)-\(safeName)"
        let fileURL = root.appendingPathComponent(relative)
        try data.write(to: fileURL, options: [.atomic])
        let item = PendingShareImport(
            id: id,
            originalFilename: safeName,
            uniformTypeIdentifier: uniformTypeIdentifier,
            relativeFilePath: relative
        )
        let manifestURL = root
            .appendingPathComponent(ShareInboxConfiguration.manifestsDirectoryName)
            .appendingPathComponent("\(id.uuidString).json")
        let encoded = try JSONEncoder().encode(item)
        try encoded.write(to: manifestURL, options: [.atomic])
        return item
    }

    public static func listPending(allowFallback: Bool = false) throws -> [PendingShareImport] {
        let root = try preparedRoot(allowFallback: allowFallback)
        let manifests = root.appendingPathComponent(ShareInboxConfiguration.manifestsDirectoryName)
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: manifests,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        return try urls
            .filter { $0.pathExtension.lowercased() == "json" }
            .compactMap { url in
                let data = try Data(contentsOf: url)
                return try JSONDecoder().decode(PendingShareImport.self, from: data)
            }
            .sorted { $0.createdAt < $1.createdAt }
    }

    public static func markConsumed(_ item: PendingShareImport, allowFallback: Bool = false) throws {
        let root = try preparedRoot(allowFallback: allowFallback)
        let manifest = root
            .appendingPathComponent(ShareInboxConfiguration.manifestsDirectoryName)
            .appendingPathComponent("\(item.id.uuidString).json")
        let file = root.appendingPathComponent(item.relativeFilePath)
        try? FileManager.default.removeItem(at: manifest)
        try? FileManager.default.removeItem(at: file)
    }

    public static func fileURL(for item: PendingShareImport, allowFallback: Bool = false) throws -> URL {
        let root = try preparedRoot(allowFallback: allowFallback)
        return root.appendingPathComponent(item.relativeFilePath)
    }

    private static func preparedRoot(allowFallback: Bool) throws -> URL {
        guard let container = containerURL(allowFallback: allowFallback) else {
            throw ShareInboxError.appGroupUnavailable
        }
        let root = container.appendingPathComponent(
            ShareInboxConfiguration.pendingDirectoryName,
            isDirectory: true
        )
        let files = root.appendingPathComponent(ShareInboxConfiguration.filesDirectoryName, isDirectory: true)
        let manifests = root.appendingPathComponent(ShareInboxConfiguration.manifestsDirectoryName, isDirectory: true)
        try FileManager.default.createDirectory(at: files, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: manifests, withIntermediateDirectories: true)
        return root
    }

    private static func sanitizedFilename(_ filename: String) -> String {
        let trimmed = filename.trimmingCharacters(in: .whitespacesAndNewlines)
        let last = (trimmed as NSString).lastPathComponent
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: ".-_"))
        let filtered = last.unicodeScalars.map { allowed.contains($0) ? Character($0) : "-" }
        let name = String(filtered)
        return name.isEmpty ? "import.bin" : name
    }
}
