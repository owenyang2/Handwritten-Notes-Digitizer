import Foundation
import CryptoKit
import XCTest
@testable import PersonalizedNotesKit

final class SharedBackendClientTests: XCTestCase {
    func testInkBundleDownloadValidatesExactContentAndUsesAuthentication() async throws {
        let pageID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let bundleID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        let bundle = InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 768, height: 1024),
            strokes: [
                InkStroke(
                    strokeID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!,
                    points: [
                        InkPoint(x: 12, y: 24, timeOffset: 0, pressure: 0.5),
                        InkPoint(x: 30, y: 48, timeOffset: 0.1, pressure: 0.75),
                    ]
                )
            ],
            nativeDrawing: NativeDrawing(data: Data("native-pair".utf8))
        )
        let path = "/pages/\(pageID.uuidString)/ink-bundles/\(bundleID.uuidString)/content"
        StubURLProtocol.reset(responses: [path: try InkV1Codec.encode(bundle)])
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let client = SharedBackendClient(
            baseURL: URL(string: "https://notes.example.test")!,
            urlSession: URLSession(configuration: configuration)
        )
        await client.setSession(
            AccountSession(userID: UUID(), accessToken: "download-token", tokenType: "bearer")
        )

        let downloaded = try await client.downloadInkBundle(pageID: pageID, bundleID: bundleID)
        XCTAssertEqual(downloaded, bundle)
        let request = try XCTUnwrap(StubURLProtocol.requests().first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "bearer download-token")

        var tampered = try XCTUnwrap(
            JSONSerialization.jsonObject(with: InkV1Codec.encode(bundle)) as? [String: Any]
        )
        tampered["content_hash"] = String(repeating: "0", count: 64)
        StubURLProtocol.reset(responses: [path: try JSONSerialization.data(withJSONObject: tampered)])
        do {
            _ = try await client.downloadInkBundle(pageID: pageID, bundleID: bundleID)
            XCTFail("A mismatched content hash must not enter the local cache")
        } catch let error as APIError {
            guard case .decoding = error else {
                return XCTFail("Expected a decoding integrity error, got \(error)")
            }
        }
    }

    func testInkBundleManifestDecodesImmutableRevisionLineage() async throws {
        let pageID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let firstBundleID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        let secondBundleID = UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
        StubURLProtocol.reset(
            responses: [
                "/pages/\(pageID.uuidString)/ink-bundles": Data(
                    """
                    {
                      "page_id": "\(pageID.uuidString)",
                      "active_bundle_id": "\(secondBundleID.uuidString)",
                      "revisions": [
                        {
                          "bundle_id": "\(secondBundleID.uuidString)",
                          "page_id": "\(pageID.uuidString)",
                          "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                          "schema_name": "ink_v1",
                          "schema_version": 1,
                          "stroke_count": 2,
                          "point_count": 4,
                          "asset_id": "44444444-4444-4444-8444-444444444444",
                          "created_at": "2026-01-01T00:00:02Z",
                          "revision_number": 2,
                          "parent_bundle_id": "\(firstBundleID.uuidString)",
                          "active": true,
                          "upload_state": "complete"
                        },
                        {
                          "bundle_id": "\(firstBundleID.uuidString)",
                          "page_id": "\(pageID.uuidString)",
                          "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                          "schema_name": "ink_v1",
                          "schema_version": 1,
                          "stroke_count": 1,
                          "point_count": 2,
                          "asset_id": "55555555-5555-4555-8555-555555555555",
                          "created_at": "2026-01-01T00:00:01Z",
                          "revision_number": 1,
                          "parent_bundle_id": null,
                          "active": false,
                          "upload_state": "complete"
                        }
                      ]
                    }
                    """.data(using: .utf8)!
                )
            ]
        )
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let client = SharedBackendClient(
            baseURL: URL(string: "https://notes.example.test")!,
            urlSession: URLSession(configuration: configuration)
        )
        await client.setSession(
            AccountSession(userID: UUID(), accessToken: "revision-token", tokenType: "bearer")
        )

        let manifest = try await client.inkBundleManifest(pageID: pageID)
        XCTAssertEqual(manifest.pageID, pageID)
        XCTAssertEqual(manifest.activeBundleID, secondBundleID)
        XCTAssertEqual(manifest.revisions.map(\.revisionNumber), [2, 1])
        XCTAssertEqual(manifest.revisions.first?.parentBundleID, firstBundleID)
        XCTAssertTrue(manifest.revisions.first?.active == true)
        XCTAssertEqual(StubURLProtocol.requests().first?.httpMethod, "GET")
        XCTAssertEqual(
            StubURLProtocol.requests().first?.value(forHTTPHeaderField: "Authorization"),
            "bearer revision-token"
        )
    }

    func testHealthProbeIsUnauthenticatedAndRecognitionRoutesCarrySession() async throws {
        let pageID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let runID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        let documentID = UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
        StubURLProtocol.reset(
            responses: [
                "/health": Data(
                    """
                    {
                      "status": "ok",
                      "capabilities": {"recognition": false},
                      "model_status": "unavailable"
                    }
                    """.utf8
                ),
                "/pages/\(pageID.uuidString)/recognition-runs": Data(
                    """
                    {
                      "run_id": "\(runID.uuidString)",
                      "document_id": "\(documentID.uuidString)",
                      "page_id": "\(pageID.uuidString)",
                      "state": "failed",
                      "scope": "page",
                      "mode": "ink",
                      "error_message": "model_unavailable",
                      "result_ids": [],
                      "created_at": "2026-01-01T00:00:00Z"
                    }
                    """.utf8
                )
            ]
        )

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let client = SharedBackendClient(
            baseURL: URL(string: "https://notes.example.test")!,
            urlSession: URLSession(configuration: configuration)
        )
        await client.setSession(
            AccountSession(
                userID: UUID(),
                accessToken: "secret-token",
                tokenType: "bearer"
            )
        )

        let health = try await client.health()
        XCTAssertEqual(health.status, "ok")
        XCTAssertEqual(health.modelStatus, "unavailable")

        let run = try await client.createRecognitionRun(
            pageID: pageID,
            request: RecognitionRunRequest(
                scope: "page",
                mode: "ink",
                idempotencyKey: UUID(uuidString: "44444444-4444-4444-8444-444444444444")
            )
        )
        XCTAssertEqual(run.runID, runID)
        XCTAssertEqual(run.errorMessage, "model_unavailable")

        let requests = StubURLProtocol.requests()
        let healthRequest = try XCTUnwrap(requests.first { $0.url?.path == "/health" })
        XCTAssertNil(healthRequest.value(forHTTPHeaderField: "Authorization"))
        let recognitionRequest = try XCTUnwrap(
            requests.first { $0.url?.path == "/pages/\(pageID.uuidString)/recognition-runs" }
        )
        XCTAssertEqual(
            recognitionRequest.value(forHTTPHeaderField: "Authorization"),
            "bearer secret-token"
        )
        XCTAssertEqual(recognitionRequest.httpMethod, "POST")
        XCTAssertEqual(
            recognitionRequest.value(forHTTPHeaderField: "Content-Type"),
            "application/json"
        )
    }

    func testPasswordRecoveryRoutesDoNotAttachAnExistingSession() async throws {
        StubURLProtocol.reset(
            responses: [
                "/auth/password-reset/request": Data(
                    "{\"status\":\"accepted\",\"debug_token\":\"development-reset-token-123456\"}".utf8
                ),
                "/auth/password-reset/confirm": Data("{\"status\":\"updated\"}".utf8),
            ]
        )
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let client = SharedBackendClient(
            baseURL: URL(string: "https://notes.example.test")!,
            urlSession: URLSession(configuration: configuration)
        )
        await client.setSession(
            AccountSession(
                userID: UUID(),
                accessToken: "must-not-be-sent",
                tokenType: "bearer"
            )
        )

        let requestResponse = try await client.requestPasswordReset(email: "person@example.com")
        XCTAssertEqual(requestResponse.status, "accepted")
        XCTAssertEqual(requestResponse.debugToken, "development-reset-token-123456")
        let confirmResponse = try await client.confirmPasswordReset(
            token: "development-reset-token-123456",
            newPassword: "new-password-123"
        )
        XCTAssertEqual(confirmResponse.status, "updated")

        for request in StubURLProtocol.requests() {
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        }
    }

    func testRegisterMeRestoreAndRevokeSessionLifecycle() async throws {
        let userID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let sessionID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        let registerResponse = """
        {
          "access_token": "fresh-token",
          "token_type": "bearer",
          "user_id": "\(userID.uuidString)"
        }
        """
        let meResponse = """
        {
          "user_id": "\(userID.uuidString)",
          "username": "ipad-user",
          "email": "person@example.com"
        }
        """
        StubURLProtocol.reset(
            responses: [
                "/auth/register": Data(registerResponse.utf8),
                "/auth/me": Data(meResponse.utf8),
                "/account/sessions/\(sessionID.uuidString)": Data(),
            ]
        )
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let baseURL = URL(string: "https://notes.example.test")!
        let client = SharedBackendClient(
            baseURL: baseURL,
            urlSession: URLSession(configuration: configuration)
        )
        let vault = InMemorySessionVault()
        let controller = AccountSessionController(vault: vault, client: client)

        let session = try await client.register(
            username: "ipad-user",
            email: "person@example.com",
            password: "correct-horse-battery-staple"
        )
        try await controller.install(session)
        XCTAssertEqual(session.userID, userID)
        let savedSession = try vault.load()
        XCTAssertEqual(savedSession?.accessToken, "fresh-token")
        let registeredMe = try await client.me()
        XCTAssertEqual(registeredMe.username, "ipad-user")

        let restoredClient = SharedBackendClient(
            baseURL: baseURL,
            urlSession: URLSession(configuration: configuration)
        )
        let restoredController = AccountSessionController(vault: vault, client: restoredClient)
        let restored = try await restoredController.restore()
        XCTAssertEqual(restored, session)
        let restoredMe = try await restoredClient.me()
        XCTAssertEqual(restoredMe.email, "person@example.com")

        try await restoredClient.revokeAccountSession(sessionID)
        try await restoredController.logout()
        let loggedOutSession = try vault.load()
        XCTAssertNil(loggedOutSession)

        let requests = StubURLProtocol.requests()
        let register = try XCTUnwrap(requests.first { $0.url?.path == "/auth/register" })
        XCTAssertNil(register.value(forHTTPHeaderField: "Authorization"))
        let restoredMeRequest = try XCTUnwrap(
            requests.filter { $0.url?.path == "/auth/me" }.last
        )
        XCTAssertEqual(
            restoredMeRequest.value(forHTTPHeaderField: "Authorization"),
            "bearer fresh-token"
        )
        let revoke = try XCTUnwrap(
            requests.first { $0.url?.path == "/account/sessions/\(sessionID.uuidString)" }
        )
        XCTAssertEqual(revoke.httpMethod, "DELETE")
        XCTAssertEqual(
            revoke.value(forHTTPHeaderField: "Authorization"),
            "bearer fresh-token"
        )
    }

    func testResumableInkUploadResumesPartsAndCompletesImmutableRevision() async throws {
        let pageID = UUID(uuidString: "11111111-1111-4111-8111-111111111111")!
        let uploadID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!
        let bundleID = UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
        let idempotencyKey = UUID(uuidString: "44444444-4444-4444-8444-444444444444")!
        let bundle = InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 612, height: 792),
            strokes: [
                InkStroke(
                    strokeID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
                    points: [InkPoint(x: 10, y: 20, timeOffset: 0)]
                )
            ]
        )
        let encoded = try InkV1Codec.encode(bundle)
        let expectedSHA256 = SHA256.hash(data: encoded)
            .map { String(format: "%02x", $0) }
            .joined()
        let createResponse = """
        {
          "upload_id": "\(uploadID.uuidString)",
          "purpose": "ink_v1",
          "filename": "\(pageID.uuidString).ink.json",
          "content_type": "application/vnd.hnd.ink-v1+json",
          "expected_size": \(encoded.count),
          "expected_sha256": "\(expectedSHA256)",
          "received_size": 0,
          "part_size": 262144,
          "status": "open",
          "document_id": null,
          "page_id": "\(pageID.uuidString)",
          "ink_bundle_id": null,
          "assembled_ref": null,
          "parts": [],
          "expires_at": "2030-01-01T00:00:00Z",
          "error_code": null,
          "error_message": null,
          "completed_at": null,
          "idempotent": false
        }
        """
        let partResponse = """
        {
          "upload_id": "\(uploadID.uuidString)",
          "purpose": "ink_v1",
          "filename": "\(pageID.uuidString).ink.json",
          "content_type": "application/vnd.hnd.ink-v1+json",
          "expected_size": \(encoded.count),
          "expected_sha256": "\(expectedSHA256)",
          "received_size": \(encoded.count),
          "part_size": 262144,
          "status": "open",
          "document_id": null,
          "page_id": "\(pageID.uuidString)",
          "ink_bundle_id": null,
          "assembled_ref": null,
          "parts": [{"part_number":1,"size_bytes":\(encoded.count),"sha256":"\(expectedSHA256)"}],
          "expires_at": "2030-01-01T00:00:00Z",
          "error_code": null,
          "error_message": null,
          "completed_at": null,
          "idempotent": false
        }
        """
        let completeResponse = """
        {
          "upload_id": "\(uploadID.uuidString)",
          "purpose": "ink_v1",
          "filename": "\(pageID.uuidString).ink.json",
          "content_type": "application/vnd.hnd.ink-v1+json",
          "expected_size": \(encoded.count),
          "expected_sha256": "\(expectedSHA256)",
          "received_size": \(encoded.count),
          "part_size": 262144,
          "status": "completed",
          "document_id": null,
          "page_id": "\(pageID.uuidString)",
          "ink_bundle_id": "\(bundleID.uuidString)",
          "assembled_ref": "assets/ink/\(bundleID.uuidString).json",
          "parts": [{"part_number":1,"size_bytes":\(encoded.count),"sha256":"\(expectedSHA256)"}],
          "expires_at": "2030-01-01T00:00:00Z",
          "error_code": null,
          "error_message": null,
          "completed_at": "2026-01-01T00:00:01Z",
          "idempotent": false
        }
        """
        do {
            _ = try APIJSON.decoder().decode(
                ResumableUploadSession.self,
                from: Data(createResponse.utf8)
            )
        } catch {
            XCTFail("resumable response does not decode: \(error)")
        }
        StubURLProtocol.reset(
            responses: [
                "/uploads": Data(createResponse.utf8),
                "/uploads/\(uploadID.uuidString)/parts/1": Data(partResponse.utf8),
                "/uploads/\(uploadID.uuidString)/complete": Data(completeResponse.utf8),
            ]
        )
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let client = SharedBackendClient(
            baseURL: URL(string: "https://notes.example.test")!,
            urlSession: URLSession(configuration: configuration)
        )
        await client.setSession(
            AccountSession(userID: UUID(), accessToken: "resumable-token", tokenType: "bearer")
        )

        let completed = try await client.uploadInkBundleResumable(
            pageID: pageID,
            bundle: bundle,
            idempotencyKey: idempotencyKey
        )
        XCTAssertEqual(completed.status, "completed")
        XCTAssertEqual(completed.uploadID, uploadID)
        XCTAssertEqual(completed.inkBundleID, bundleID)

        let requests = StubURLProtocol.requests()
        XCTAssertEqual(requests.count, 3)
        let create = try XCTUnwrap(requests.first { $0.url?.path == "/uploads" })
        XCTAssertEqual(create.httpMethod, "POST")
        let createBody = try XCTUnwrap(create.httpBody)
        let createJSON = try XCTUnwrap(
            JSONSerialization.jsonObject(with: createBody) as? [String: Any]
        )
        XCTAssertEqual(createJSON["purpose"] as? String, "ink_v1")
        XCTAssertEqual(createJSON["page_id"] as? String, pageID.uuidString)
        XCTAssertEqual(createJSON["idempotency_key"] as? String, idempotencyKey.uuidString)
        XCTAssertEqual(createJSON["expected_sha256"] as? String, expectedSHA256)

        let part = try XCTUnwrap(requests.first { $0.url?.path.contains("/parts/1") == true })
        XCTAssertEqual(part.httpMethod, "PUT")
        XCTAssertEqual(part.value(forHTTPHeaderField: "Content-Type"), "application/octet-stream")
        XCTAssertEqual(part.value(forHTTPHeaderField: "X-Part-SHA256"), expectedSHA256)
        XCTAssertEqual(part.httpBody, encoded)

        let complete = try XCTUnwrap(requests.first { $0.url?.path.hasSuffix("/complete") == true })
        XCTAssertEqual(complete.httpMethod, "POST")
    }
}

private final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) private static var responseData: [String: Data] = [:]
    nonisolated(unsafe) private static var capturedRequests: [URLRequest] = []
    private static let lock = NSLock()

    static func reset(responses: [String: Data]) {
        lock.lock()
        defer { lock.unlock() }
        responseData = responses
        capturedRequests = []
    }

    static func requests() -> [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return capturedRequests
    }

    override class func canInit(with request: URLRequest) -> Bool {
        request.url?.host == "notes.example.test"
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let url = request.url else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }
        var capturedRequest = request
        if capturedRequest.httpBody == nil, let bodyStream = capturedRequest.httpBodyStream {
            bodyStream.open()
            defer { bodyStream.close() }
            let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 64 * 1024)
            defer { buffer.deallocate() }
            var body = Data()
            while bodyStream.hasBytesAvailable {
                let count = bodyStream.read(buffer, maxLength: 64 * 1024)
                if count <= 0 { break }
                body.append(buffer, count: count)
            }
            capturedRequest.httpBody = body
        }
        Self.lock.lock()
        Self.capturedRequests.append(capturedRequest)
        let data = Self.responseData[url.path]
        Self.lock.unlock()
        guard let data else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }
        let response = HTTPURLResponse(
            url: url,
            statusCode: 200,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
