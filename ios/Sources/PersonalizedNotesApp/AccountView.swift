import PersonalizedNotesKit
import SwiftUI

struct AccountView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var sessions: [AccountSessionInfo] = []
    @State private var me: MeResponse?
    @State private var loading = true
    @State private var localError: String?
    @State private var shareableExport: ShareableExportFile?
    @State private var showingDeleteConfirmation = false
    @State private var deleteConfirmation = ""

    var body: some View {
        List {
            if let me {
                signedInSection(me)
            }
            sessionsSection
            dataSection
        }
        .navigationTitle("Account")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
            ToolbarItem(placement: .primaryAction) {
                Button("Refresh") { Task { await load() } }
            }
        }
        .task { await load() }
        .alert(
            "Account error",
            isPresented: Binding(
                get: { localError != nil },
                set: { if !$0 { localError = nil } }
            )
        ) {
            Button("OK", role: .cancel) { localError = nil }
        } message: {
            Text(localError ?? "")
        }
        .alert("Delete account", isPresented: $showingDeleteConfirmation) {
            TextField("Type DELETE", text: $deleteConfirmation)
                #if os(iOS)
                .textInputAutocapitalization(.characters)
                #endif
            Button("Delete account", role: .destructive) {
                Task { await deleteAccount() }
            }
            .disabled(deleteConfirmation != "DELETE")
            Button("Cancel", role: .cancel) { deleteConfirmation = "" }
        } message: {
            Text("Type DELETE to confirm. Server notebooks and account sessions will be revoked. Local notebooks on this iPad are kept.")
        }
        .sheet(item: $shareableExport) { file in
            #if canImport(UIKit)
            ActivityShareView(file: file)
            #else
            Text(file.url.path)
            #endif
        }
    }

    private func signedInSection(_ me: MeResponse) -> some View {
        Section("Signed in") {
            LabeledContent("Username", value: me.username)
            LabeledContent("Email", value: me.email)
            LabeledContent("User ID", value: me.userID.uuidString)
        }
    }

    private var sessionsSection: some View {
        Section("Sessions") {
            if loading {
                ProgressView()
            } else if sessions.isEmpty {
                Text("No sessions were returned. The device can still use the local library.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(sessions) { session in
                    sessionRow(session)
                }
            }
        }
    }

    private func sessionRow(_ session: AccountSessionInfo) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(session.deviceName ?? session.userAgent ?? "Unknown device")
            Text(sessionDetail(session))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .swipeActions {
            if !session.isCurrent && session.revokedAt == nil {
                Button("Revoke", role: .destructive) {
                    Task { await revoke(session) }
                }
            }
        }
    }

    private var dataSection: some View {
        Section("Data and account") {
            Button("Export account receipt", systemImage: "square.and.arrow.up") {
                Task { await exportAccount() }
            }
            Text("This currently exports a server-generated metadata receipt. Your local notebooks remain on this iPad unless you remove them separately.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Button("Delete account", role: .destructive) {
                deleteConfirmation = ""
                showingDeleteConfirmation = true
            }
            Text("This revokes the account, marks its server library for deletion, and cannot be undone. The local-first copy on this iPad is not automatically erased.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            me = try await model.client.me()
            sessions = try await model.client.listAccountSessions()
        } catch {
            localError = error.localizedDescription
        }
    }

    private func revoke(_ session: AccountSessionInfo) async {
        do {
            try await model.client.revokeAccountSession(session.sessionID)
            sessions = try await model.client.listAccountSessions()
        } catch {
            localError = error.localizedDescription
        }
    }

    private func exportAccount() async {
        do {
            let response = try await model.client.exportAccount()
            let url = try LocalAccountExportWriter.write(response)
            shareableExport = ShareableExportFile(url: url)
        } catch {
            localError = error.localizedDescription
        }
    }

    private func deleteAccount() async {
        guard deleteConfirmation == "DELETE" else { return }
        do {
            let response = try await model.client.deleteAccount()
            guard response.status == "completed" else {
                throw APIError.server(
                    statusCode: 500,
                    code: "account_deletion_incomplete",
                    message: response.errorMessage
                )
            }
            await model.logout()
            dismiss()
        } catch {
            localError = error.localizedDescription
        }
    }

    private func sessionDetail(_ session: AccountSessionInfo) -> String {
        var parts: [String] = []
        if session.isCurrent { parts.append("This iPad") }
        if session.revokedAt != nil { parts.append("Revoked") }
        parts.append("Created \(session.createdAt.formatted(date: .abbreviated, time: .shortened))")
        if let lastSeen = session.lastSeenAt {
            parts.append("Last seen \(lastSeen.formatted(date: .abbreviated, time: .shortened))")
        }
        return parts.joined(separator: " · ")
    }
}
