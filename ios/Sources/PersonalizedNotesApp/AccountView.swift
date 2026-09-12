import PersonalizedNotesKit
import SwiftUI

struct AccountView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var sessions: [AccountSessionInfo] = []
    @State private var me: MeResponse?
    @State private var loading = true
    @State private var localError: String?

    var body: some View {
        List {
            if let me {
                Section("Signed in") {
                    LabeledContent("Username", value: me.username)
                    LabeledContent("Email", value: me.email)
                    LabeledContent("User ID", value: me.userID.uuidString)
                }
            }
            Section("Sessions") {
                if loading {
                    ProgressView()
                } else if sessions.isEmpty {
                    Text("No sessions were returned. The device can still use the local library.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(sessions) { session in
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
                }
            }
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
