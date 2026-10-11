import Foundation

public struct AccountSession: Codable, Equatable, Sendable {
    public let userID: UUID
    public let accessToken: String
    public let tokenType: String
    public let expiresAt: Date?

    public init(
        userID: UUID,
        accessToken: String,
        tokenType: String = "bearer",
        expiresAt: Date? = nil
    ) {
        self.userID = userID
        self.accessToken = accessToken
        self.tokenType = tokenType
        self.expiresAt = expiresAt
    }
}

public protocol SessionVault: Sendable {
    func load() throws -> AccountSession?
    func save(_ session: AccountSession) throws
    func remove() throws
}

public enum SessionVaultError: Error, Equatable, Sendable {
    case keychain(OSStatusCode)
    case invalidStoredSession
}

extension SessionVaultError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidStoredSession:
            return "The saved sign-in session could not be read. Sign in again."
        case .keychain(let status):
            #if canImport(Security)
            let detail = SecCopyErrorMessageString(status.rawValue, nil) as String? ?? "Unknown Keychain error"
            return "Secure sign-in storage failed: \(detail) (\(status.rawValue))."
            #else
            return "Secure sign-in storage failed (\(status.rawValue))."
            #endif
        }
    }
}

/// A platform-independent wrapper for the small subset of OSStatus needed in
/// the public error surface. This keeps the core package testable without
/// leaking Security.framework types through every call site.
public struct OSStatusCode: RawRepresentable, Equatable, Sendable {
    public let rawValue: Int32

    public init(rawValue: Int32) {
        self.rawValue = rawValue
    }
}

public final class InMemorySessionVault: SessionVault, @unchecked Sendable {
    private let lock = NSLock()
    private var session: AccountSession?

    public init(session: AccountSession? = nil) {
        self.session = session
    }

    public func load() throws -> AccountSession? {
        lock.lock()
        defer { lock.unlock() }
        return session
    }

    public func save(_ session: AccountSession) throws {
        lock.lock()
        self.session = session
        lock.unlock()
    }

    public func remove() throws {
        lock.lock()
        session = nil
        lock.unlock()
    }
}

#if canImport(Security)
import Security

public final class KeychainSessionVault: SessionVault, @unchecked Sendable {
    private let service: String
    private let account: String

    public init(
        service: String = "com.handwrittennotes.personalized-notes",
        account: String = "account-session"
    ) {
        self.service = service
        self.account = account
    }

    public func load() throws -> AccountSession? {
        var result: CFTypeRef?
        var query = baseQuery()
        query[kSecReturnData] = true
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw SessionVaultError.keychain(OSStatusCode(rawValue: status))
        }
        guard let data = result as? Data else {
            throw SessionVaultError.invalidStoredSession
        }
        do {
            return try APIJSON.decoder().decode(AccountSession.self, from: data)
        } catch {
            throw SessionVaultError.invalidStoredSession
        }
    }

    public func save(_ session: AccountSession) throws {
        let data: Data
        do {
            data = try APIJSON.encoder().encode(session)
        } catch {
            throw SessionVaultError.invalidStoredSession
        }

        let query = baseQuery() as CFDictionary
        let update: [CFString: Any] = [kSecValueData: data]
        let updateStatus = SecItemUpdate(query, update as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var add = baseQuery()
            add[kSecValueData] = data
            add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let addStatus = SecItemAdd(add as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw SessionVaultError.keychain(OSStatusCode(rawValue: addStatus))
            }
        } else if updateStatus != errSecSuccess {
            throw SessionVaultError.keychain(OSStatusCode(rawValue: updateStatus))
        }
    }

    public func remove() throws {
        let status = SecItemDelete(baseQuery() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SessionVaultError.keychain(OSStatusCode(rawValue: status))
        }
    }

    private func baseQuery() -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ]
    }
}
#endif

/// Owns the single account session and keeps the transport credential in sync
/// with the durable vault. Native UI code never needs to read a token directly.
public actor AccountSessionController {
    private let vault: any SessionVault
    private let client: SharedBackendClient
    private var current: AccountSession?

    public init(vault: any SessionVault, client: SharedBackendClient) {
        self.vault = vault
        self.client = client
    }

    public func restore() async throws -> AccountSession? {
        let session = try vault.load()
        current = session
        await client.setSession(session)
        return session
    }

    public func install(_ session: AccountSession) async throws {
        try vault.save(session)
        current = session
        await client.setSession(session)
    }

    public func logout() async throws {
        try vault.remove()
        current = nil
        await client.setSession(nil)
    }

    public func session() -> AccountSession? { current }
}
