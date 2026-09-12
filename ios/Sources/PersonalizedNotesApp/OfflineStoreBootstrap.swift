import Foundation
import PersonalizedNotesKit

/// Opens the on-device SQLite store and related local caches. Network is never
/// required for this path — a cold launch must still show the last library.
enum OfflineStoreBootstrap {
    static let clientIDDefaultsKey = "personalized-notes.client-id"
    static let backendURLDefaultsKey = "personalized-notes.backend-url"
    static let defaultBackendURL = URL(string: "http://127.0.0.1:8000")!

    static func applicationSupportDirectory() throws -> URL {
        let directory = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PersonalizedNotes", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func openStore() throws -> SQLiteLocalStore {
        try SQLiteLocalStore(applicationSupportDirectory: try applicationSupportDirectory())
    }

    static func clientID() -> String {
        let defaults = UserDefaults.standard
        if let existing = defaults.string(forKey: clientIDDefaultsKey), !existing.isEmpty {
            return existing
        }
        let created = "ipad-\(UUID().uuidString.lowercased())"
        defaults.set(created, forKey: clientIDDefaultsKey)
        return created
    }

    static func backendURL() -> URL {
        let defaults = UserDefaults.standard
        if let raw = defaults.string(forKey: backendURLDefaultsKey),
           let url = URL(string: raw),
           url.scheme != nil,
           url.host != nil {
            return url
        }
        return defaultBackendURL
    }

    static func sessionVault() -> any SessionVault {
        #if canImport(Security)
        return KeychainSessionVault()
        #else
        return InMemorySessionVault()
        #endif
    }
}

/// Local PKDrawing / ink_v1 files. Commits stay on disk until a later sync
/// pass decides an upload is allowed (for example Wi-Fi only).
enum LocalInkCache {
    static func directory() throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("ink", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func pkDrawingURL(pageID: UUID) throws -> URL {
        try directory().appendingPathComponent("\(pageID.uuidString).pkdrawing")
    }

    static func inkBundleURL(pageID: UUID) throws -> URL {
        try directory().appendingPathComponent("\(pageID.uuidString).ink.json")
    }

    static func readPKDrawing(pageID: UUID) -> Data? {
        guard let url = try? pkDrawingURL(pageID: pageID) else { return nil }
        return try? Data(contentsOf: url)
    }

    static func writePKDrawing(_ data: Data, pageID: UUID) throws {
        guard !data.isEmpty else { return }
        try data.write(to: pkDrawingURL(pageID: pageID), options: [.atomic])
    }

    static func writeInkBundle(_ bundle: InkBundle) throws {
        try InkV1Codec.write(bundle, to: inkBundleURL(pageID: bundle.pageID))
    }
}
