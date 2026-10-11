import Foundation
import CryptoKit

/// The transport boundary shared by the native client and the existing
/// FastAPI service. It deliberately exposes typed domain values rather than
/// browser-specific `File`/`Blob` APIs.
public actor SharedBackendClient: SyncService {
    public private(set) var baseURL: URL
    private let urlSession: URLSession
    private var accountSession: AccountSession?

    public init(baseURL: URL, urlSession: URLSession = .shared) {
        self.baseURL = baseURL
        self.urlSession = urlSession
    }

    public func setSession(_ session: AccountSession?) {
        accountSession = session
    }

    public func setBaseURL(_ url: URL) {
        baseURL = url
    }

    public func health() async throws -> BackendHealthResponse {
        let request = try makeRequest(
            url: makeURL(path: "health"),
            method: "GET",
            includeSession: false
        )
        return try await send(request)
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

    public func requestPasswordReset(email: String) async throws -> PasswordResetResponse {
        try await send(
            path: "auth/password-reset/request",
            method: "POST",
            body: PasswordResetRequest(email: email),
            includeSession: false
        )
    }

    public func confirmPasswordReset(
        token: String,
        newPassword: String
    ) async throws -> PasswordResetConfirmResponse {
        try await send(
            path: "auth/password-reset/confirm",
            method: "POST",
            body: PasswordResetConfirmRequest(token: token, newPassword: newPassword),
            includeSession: false
        )
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

    public func exportAccount() async throws -> AccountExportResponse {
        try await send(path: "account/export", method: "POST", body: Optional<EmptyBody>.none)
    }

    public func deleteAccount() async throws -> AccountDeletionResponse {
        try await send(
            path: "account",
            method: "DELETE",
            body: DeleteAccountRequest(confirmation: "DELETE")
        )
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

    /// Publishes an ink revision through the durable resumable-upload protocol.
    ///
    /// The idempotency key must be stable for the local revision. If the app is
    /// terminated after creating the session or after any individual part, a
    /// retry receives the same server manifest and only sends missing parts.
    public func uploadInkBundleResumable(
        pageID: UUID,
        bundle: InkBundle,
        idempotencyKey: UUID,
        partSize: Int? = nil
    ) async throws -> ResumableUploadSession {
        let bytes: Data
        do {
            bytes = try InkV1Codec.encode(bundle)
        } catch {
            throw APIError.encoding(error.localizedDescription)
        }
        let expectedSHA256 = Self.sha256(bytes)
        var session: ResumableUploadSession = try await send(
            path: "uploads",
            method: "POST",
            body: CreateResumableUploadRequest(
                purpose: "ink_v1",
                filename: "\(pageID.uuidString).ink.json",
                contentType: "application/vnd.hnd.ink-v1+json",
                expectedSize: bytes.count,
                expectedSHA256: expectedSHA256,
                partSize: partSize,
                idempotencyKey: idempotencyKey,
                pageID: pageID
            )
        )

        guard session.purpose == "ink_v1",
              session.pageID == pageID,
              session.expectedSize == bytes.count,
              session.expectedSHA256.lowercased() == expectedSHA256 else {
            throw APIError.server(
                statusCode: 409,
                code: "upload_conflict",
                message: "The resumable ink session does not match the local revision."
            )
        }
        if session.status == "completed" {
            return session
        }
        guard session.status == "open" else {
            throw APIError.server(
                statusCode: 409,
                code: session.errorCode ?? "upload_conflict",
                message: session.errorMessage ?? "The resumable ink session is \(session.status)."
            )
        }

        let recordedParts: [Int: ResumableUploadPart] = Dictionary(
            uniqueKeysWithValues: session.parts.map { ($0.partNumber, $0) }
        )
        let effectivePartSize = max(1, session.partSize)
        var offset = 0
        var partNumber = 1
        while offset < bytes.count {
            let end = min(bytes.count, offset + effectivePartSize)
            let part = bytes.subdata(in: offset..<end)
            let digest = Self.sha256(part)
            if let recorded = recordedParts[partNumber] {
                guard recorded.sizeBytes == part.count,
                      recorded.sha256.lowercased() == digest else {
                    throw APIError.server(
                        statusCode: 409,
                        code: "upload_conflict",
                        message: "The server has a different digest for upload part \(partNumber)."
                    )
                }
            } else {
                session = try await putResumablePart(
                    uploadID: session.uploadID,
                    partNumber: partNumber,
                    data: part,
                    digest: digest
                )
            }
            offset = end
            partNumber += 1
        }

        let completed: ResumableUploadSession = try await send(
            path: "uploads/\(session.uploadID.uuidString)/complete",
            method: "POST",
            body: Optional<EmptyBody>.none
        )
        guard completed.status == "completed", completed.inkBundleID != nil else {
            throw APIError.server(
                statusCode: 422,
                code: completed.errorCode ?? "ink_integrity_failure",
                message: completed.errorMessage ?? "The server did not publish the ink revision."
            )
        }
        return completed
    }

    public func inkBundleManifest(pageID: UUID) async throws -> InkBundleManifestResponse {
        try await send(
            path: "pages/\(pageID.uuidString)/ink-bundles",
            method: "GET",
            body: Optional<EmptyBody>.none
        )
    }

    public func downloadInkBundle(pageID: UUID, bundleID: UUID) async throws -> InkBundle {
        let response: InkBundleContentResponse = try await send(
            path: "pages/\(pageID.uuidString)/ink-bundles/\(bundleID.uuidString)/content",
            method: "GET",
            body: Optional<EmptyBody>.none
        )
        guard response.pageID == pageID else {
            throw APIError.decoding("The downloaded ink revision belongs to a different page.")
        }
        do {
            return try response.validatedBundle()
        } catch {
            throw APIError.decoding("The downloaded ink revision failed its integrity check.")
        }
    }

    public func createRecognitionRun(
        pageID: UUID,
        request: RecognitionRunRequest
    ) async throws -> RecognitionRun {
        try await send(
            path: "pages/\(pageID.uuidString)/recognition-runs",
            method: "POST",
            body: request
        )
    }

    public func getRecognitionRun(runID: UUID) async throws -> RecognitionRun {
        try await send(
            path: "recognition-runs/\(runID.uuidString)",
            method: "GET",
            body: Optional<EmptyBody>.none
        )
    }

    public func conversionBlocks(runID: UUID) async throws -> [ConversionBlock] {
        try await send(path: "recognition-runs/\(runID.uuidString)/results", method: "GET", body: Optional<EmptyBody>.none)
    }

    public func pageRegions(pageID: UUID) async throws -> [ConversionBlock] {
        try await send(path: "pages/\(pageID.uuidString)/regions", method: "GET", body: Optional<EmptyBody>.none)
    }

    public func conversionSource(runID: UUID) async throws -> Data {
        let request = try makeRequest(url: makeURL(path: "recognition-runs/\(runID.uuidString)/source"), method: "GET")
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw serverError(statusCode: http.statusCode, data: data) }
        return data
    }

    public func saveConversionReview(block: ConversionBlock, text: String, excluded: Bool) async throws -> SavedRegionReview {
        var body: JSONDocument = ["reviewed_text": .string(text), "excluded": .bool(excluded)]
        if let value = block.predictionID { body["prediction_id"] = .string(value.uuidString) }
        if let value = block.reviewVersion { body["expected_version"] = .number(Double(value)) }
        return try await send(path: "regions/\(block.regionID.uuidString)/review", method: "POST", body: body)
    }

    public func exportConvertedDocument(documentID: UUID, format: String, revisionID: UUID?) async throws -> ConvertedDocumentExport {
        var body: JSONDocument = ["format": .string(format)]
        if let revisionID { body["revision_id"] = .string(revisionID.uuidString) }
        return try await send(path: "documents/\(documentID.uuidString)/exports", method: "POST", body: body)
    }

    public func splitConversionRegion(regionID: UUID, boxes: [[Int]]) async throws {
        let body: JSONDocument = ["boxes": .array(boxes.map { .array($0.map { .number(Double($0)) }) })]
        let _: [JSONDocument] = try await send(path: "regions/\(regionID.uuidString)/split", method: "POST", body: body)
    }

    public func mergeConversionRegions(pageID: UUID, regionIDs: [UUID]) async throws {
        let body: JSONDocument = ["region_ids": .array(regionIDs.map { .string($0.uuidString) })]
        let _: JSONDocument = try await send(path: "pages/\(pageID.uuidString)/regions/merge", method: "POST", body: body)
    }

    public func reorderConversionRegions(pageID: UUID, regionIDs: [UUID]) async throws {
        let body: JSONDocument = ["region_ids": .array(regionIDs.map { .string($0.uuidString) })]
        let _: [JSONDocument] = try await send(path: "pages/\(pageID.uuidString)/regions/reorder", method: "POST", body: body)
    }

    /// Creates the authenticated request used by the background file session.
    /// The file must contain the exact bytes returned by `InkV1Codec.encode`.
    public func makeInkBundleUploadRequest(pageID: UUID) throws -> URLRequest {
        let url = try makeURL(path: "pages/\(pageID.uuidString)/ink-bundles")
        var request = makeRequest(url: url, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return request
    }

    /// Uploads a durable PDF/image source to an already-synced document.
    /// Multipart bytes are staged in a temporary file so large PDFs do not
    /// need to be held entirely in the app's transient memory budget.
    public func uploadDocumentSource(
        documentID: UUID,
        fileURL: URL,
        originalFilename: String,
        mimeType: String?
    ) async throws -> DocumentSourceUploadResponse {
        let url = try makeURL(path: "documents/\(documentID.uuidString)/source")
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = makeRequest(url: url, method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let bodyURL = try makeMultipartBodyFile(
            boundary: boundary,
            fileURL: fileURL,
            filename: originalFilename,
            mimeType: mimeType ?? "application/octet-stream"
        )
        defer { try? FileManager.default.removeItem(at: bodyURL) }
        do {
            try Task.checkCancellation()
            let (data, response) = try await urlSession.upload(for: request, fromFile: bodyURL)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                throw serverError(statusCode: httpResponse.statusCode, data: data)
            }
            do {
                return try APIJSON.decoder().decode(DocumentSourceUploadResponse.self, from: data)
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

    public func downloadDocumentSource(documentID: UUID) async throws -> URL {
        let url = try makeURL(path: "documents/\(documentID.uuidString)/source")
        let request = makeRequest(url: url, method: "GET")
        do {
            try Task.checkCancellation()
            let (temporaryURL, response) = try await urlSession.download(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                let data = (try? Data(contentsOf: temporaryURL)) ?? Data()
                throw serverError(statusCode: httpResponse.statusCode, data: data)
            }
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("source-download-\(UUID().uuidString)")
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: temporaryURL, to: destination)
            return destination
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

    private func putResumablePart(
        uploadID: UUID,
        partNumber: Int,
        data: Data,
        digest: String
    ) async throws -> ResumableUploadSession {
        let url = try makeURL(path: "uploads/\(uploadID.uuidString)/parts/\(partNumber)")
        var request = makeRequest(url: url, method: "PUT", body: data)
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(digest, forHTTPHeaderField: "X-Part-SHA256")
        return try await send(request)
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func send<Response: Decodable, Body: Encodable>(
        path: String,
        method: String,
        body: Body?,
        includeSession: Bool = true
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
            body: bodyData,
            includeSession: includeSession
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
        body: Data? = nil,
        includeSession: Bool = true
    ) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if includeSession, let accountSession {
            request.setValue(
                "\(accountSession.tokenType) \(accountSession.accessToken)",
                forHTTPHeaderField: "Authorization"
            )
        }
        return request
    }

    private func makeMultipartBodyFile(
        boundary: String,
        fileURL: URL,
        filename: String,
        mimeType: String
    ) throws -> URL {
        let temporaryURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("source-upload-\(UUID().uuidString).multipart")
        guard FileManager.default.createFile(atPath: temporaryURL.path, contents: nil) else {
            throw APIError.transport("Could not create the upload staging file.")
        }
        do {
            let output = try FileHandle(forWritingTo: temporaryURL)
            let input = try FileHandle(forReadingFrom: fileURL)
            defer {
                try? input.close()
                try? output.close()
            }
            let safeFilename = URL(fileURLWithPath: filename).lastPathComponent
                .replacingOccurrences(of: "\"", with: "_")
            let prefix = "--\(boundary)\r\n"
                + "Content-Disposition: form-data; name=\"file\"; filename=\"\(safeFilename)\"\r\n"
                + "Content-Type: \(mimeType)\r\n\r\n"
            try output.write(contentsOf: Data(prefix.utf8))
            while let chunk = try input.read(upToCount: 1024 * 1024), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }
            try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
            return temporaryURL
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        }
    }
}
