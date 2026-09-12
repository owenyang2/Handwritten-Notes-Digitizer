import Foundation

/// The transport boundary shared by the native client and the existing
/// FastAPI service. It deliberately exposes typed domain values rather than
/// browser-specific `File`/`Blob` APIs.
public actor SharedBackendClient: SyncService {
    public let baseURL: URL
    private let urlSession: URLSession
    private var accountSession: AccountSession?

    public init(baseURL: URL, urlSession: URLSession = .shared) {
        self.baseURL = baseURL
        self.urlSession = urlSession
    }

    public func setSession(_ session: AccountSession?) {
        accountSession = session
    }

    public func login(username: String, password: String) async throws -> AccountSession {
        let response: LoginResponse = try await send(
            path: "auth/login",
            method: "POST",
            body: LoginRequest(username: username, password: password)
        )
        let session = AccountSession(
            userID: response.userID,
            accessToken: response.accessToken,
            tokenType: response.tokenType
        )
        accountSession = session
        return session
    }

    public func register(
        username: String,
        email: String,
        password: String
    ) async throws -> AccountSession {
        let response: LoginResponse = try await send(
            path: "auth/register",
            method: "POST",
            body: RegisterRequest(username: username, email: email, password: password)
        )
        let session = AccountSession(
            userID: response.userID,
            accessToken: response.accessToken,
            tokenType: response.tokenType
        )
        accountSession = session
        return session
    }

    public func me() async throws -> MeResponse {
        try await send(path: "auth/me", method: "GET", body: Optional<EmptyBody>.none)
    }

    public func listAccountSessions() async throws -> [AccountSessionInfo] {
        try await send(path: "account/sessions", method: "GET", body: Optional<EmptyBody>.none)
    }

    public func revokeAccountSession(_ sessionID: UUID) async throws {
        let request = try makeRequest(
            url: makeURL(path: "account/sessions/\(sessionID.uuidString)"),
            method: "DELETE"
        )
        try await sendVoid(request)
    }

    public func listDocuments(_ query: DocumentListQuery = DocumentListQuery()) async throws -> [DocumentRecord] {
        let requestURL = try makeURL(
            path: "documents",
            queryItems: [
                query.query.map { URLQueryItem(name: "query", value: $0) },
                URLQueryItem(name: "view", value: query.view.rawValue),
                URLQueryItem(name: "limit", value: String(query.limit)),
                URLQueryItem(name: "offset", value: String(query.offset)),
            ].compactMap { $0 }
        )
        return try await send(makeRequest(url: requestURL, method: "GET"))
    }

    public func getDocument(_ documentID: UUID) async throws -> DocumentRecord {
        try await send(path: "documents/\(documentID.uuidString)", method: "GET", body: Optional<EmptyBody>.none)
    }

    public func createDocument(_ request: CreateDocumentRequest) async throws -> DocumentRecord {
        try await send(path: "documents", method: "POST", body: request)
    }

    public func updateDocument(
        _ documentID: UUID,
        request: UpdateDocumentRequest
    ) async throws -> DocumentRecord {
        try await send(path: "documents/\(documentID.uuidString)", method: "PATCH", body: request)
    }

    public func trashDocument(_ documentID: UUID) async throws -> DocumentRecord {
        try await send(path: "documents/\(documentID.uuidString)", method: "DELETE", body: Optional<EmptyBody>.none)
    }

    public func restoreDocument(_ documentID: UUID) async throws -> DocumentRecord {
        try await send(path: "documents/\(documentID.uuidString)/restore", method: "POST", body: Optional<EmptyBody>.none)
    }

    public func uploadInkBundle(
        pageID: UUID,
        bundle: InkBundle
    ) async throws -> InkBundleUploadResponse {
        try await send(path: "pages/\(pageID.uuidString)/ink-bundles", method: "POST", body: bundle)
    }

    /// Creates the authenticated request used by the background file session.
    /// The file must contain the exact bytes returned by `InkV1Codec.encode`.
    public func makeInkBundleUploadRequest(pageID: UUID) throws -> URLRequest {
        let url = try makeURL(path: "pages/\(pageID.uuidString)/ink-bundles")
        var request = makeRequest(url: url, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return request
    }

    public func push(_ request: SyncPushRequest) async throws -> SyncPushResponse {
        try await send(path: "sync/push", method: "POST", body: request)
    }

    public func pull(cursor: Int, limit: Int = 50) async throws -> SyncPullResponse {
        let requestURL = try makeURL(
            path: "sync/pull",
            queryItems: [
                URLQueryItem(name: "cursor", value: String(max(0, cursor))),
                URLQueryItem(name: "limit", value: String(max(1, min(limit, 100)))),
            ]
        )
        return try await send(makeRequest(url: requestURL, method: "GET"))
    }

    public func conflicts(includeResolved: Bool = false) async throws -> [SyncConflict] {
        let requestURL = try makeURL(
            path: "sync/conflicts",
            queryItems: [URLQueryItem(name: "include_resolved", value: String(includeResolved))]
        )
        return try await send(makeRequest(url: requestURL, method: "GET"))
    }

    public func resolveConflict(
        conflictID: UUID,
        request: ResolveSyncConflictRequest
    ) async throws -> SyncConflict {
        try await send(
            path: "sync/conflicts/\(conflictID.uuidString)/resolve",
            method: "POST",
            body: request
        )
    }

    private struct EmptyBody: Encodable {}

    private func send<Response: Decodable, Body: Encodable>(
        path: String,
        method: String,
        body: Body?
    ) async throws -> Response {
        let bodyData: Data?
        if let body {
            do {
                bodyData = try APIJSON.encoder().encode(body)
            } catch {
                throw APIError.encoding(error.localizedDescription)
            }
        } else {
            bodyData = nil
        }
        let request = try makeRequest(
            url: makeURL(path: path),
            method: method,
            body: bodyData
        )
        return try await send(request)
    }

    private func sendVoid(_ request: URLRequest) async throws {
        do {
            try Task.checkCancellation()
            let (data, response) = try await urlSession.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                throw serverError(statusCode: httpResponse.statusCode, data: data)
            }
        } catch is CancellationError {
            throw APIError.cancelled
        } catch let error as URLError where error.code == .cancelled {
            throw APIError.cancelled
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
    }

    private func send<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        do {
            try Task.checkCancellation()
            let (data, response) = try await urlSession.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                throw serverError(statusCode: httpResponse.statusCode, data: data)
            }
            do {
                return try APIJSON.decoder().decode(Response.self, from: data)
            } catch {
                throw APIError.decoding(error.localizedDescription)
            }
        } catch is CancellationError {
            throw APIError.cancelled
        } catch let error as URLError where error.code == .cancelled {
            throw APIError.cancelled
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
    }

    private func serverError(statusCode: Int, data: Data) -> APIError {
        struct ErrorBody: Decodable {
            let code: String?
            let message: String?
            let detail: String?
        }
        let body = try? APIJSON.decoder().decode(ErrorBody.self, from: data)
        return .server(
            statusCode: statusCode,
            code: body?.code,
            message: body?.message ?? body?.detail
        )
    }

    private func makeURL(
        path: String,
        queryItems: [URLQueryItem] = []
    ) throws -> URL {
        guard baseURL.scheme != nil, baseURL.host != nil else {
            throw APIError.invalidBaseURL
        }
        let cleanPath = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        var url = baseURL.appendingPathComponent(cleanPath)
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidBaseURL
        }
        components.queryItems = queryItems.isEmpty ? nil : queryItems
        guard let queriedURL = components.url else { throw APIError.invalidBaseURL }
        url = queriedURL
        return url
    }

    private func makeRequest(
        url: URL,
        method: String,
        body: Data? = nil
    ) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let accountSession {
            request.setValue(
                "\(accountSession.tokenType) \(accountSession.accessToken)",
                forHTTPHeaderField: "Authorization"
            )
        }
        return request
    }
}
