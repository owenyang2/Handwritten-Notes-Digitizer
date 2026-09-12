import PersonalizedNotesKit
import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var newTitle = ""
    @State private var showingCreate = false
    @State private var showingAccount = false
    @State private var showingSettings = false

    var body: some View {
        List {
            Section {
                syncStatusRow
            }
            Section(sectionTitle) {
                if model.documents.isEmpty {
                    Text(emptyText)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.documents) { document in
                        Button {
                            model.openNotebook(document.documentID)
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(document.title)
                                    .foregroundStyle(.primary)
                                Text(subtitle(for: document))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            trailingActions(for: document)
                        }
                    }
                }
            }
        }
        .navigationTitle("Library")
        .searchable(text: $model.searchQuery)
        .onChange(of: model.searchQuery) { _, _ in
            Task { await model.refreshLibrary() }
        }
        .onChange(of: model.libraryView) { _, _ in
            Task { await model.refreshLibrary() }
        }
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("View", selection: $model.libraryView) {
                    ForEach(DocumentLibraryView.allCases, id: \.self) { view in
                        Text(view.label).tag(view)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 360)
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showingCreate = true
                } label: {
                    Label("New Notebook", systemImage: "square.and.pencil")
                }
                .disabled(model.libraryView != .active)
            }
            ToolbarItem(placement: leadingPlacement) {
                Menu {
                    Button("Account") { showingAccount = true }
                    Button("Settings") { showingSettings = true }
                    Button("Sign Out", role: .destructive) {
                        Task { await model.logout() }
                    }
                } label: {
                    Label("More", systemImage: "ellipsis.circle")
                }
            }
        }
        .refreshable {
            await model.syncNow()
        }
        .alert("New Notebook", isPresented: $showingCreate) {
            TextField("Title", text: $newTitle)
            Button("Create") {
                let title = newTitle
                newTitle = ""
                Task { await model.createNotebook(title: title) }
            }
            Button("Cancel", role: .cancel) { newTitle = "" }
        }
        .sheet(isPresented: $showingAccount) {
            NavigationStack { AccountView() }
        }
        .sheet(isPresented: $showingSettings) {
            NavigationStack { SettingsView() }
        }
    }

    private var leadingPlacement: ToolbarItemPlacement {
        #if os(iOS)
        .topBarLeading
        #else
        .automatic
        #endif
    }

    private var syncStatusRow: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(syncTitle)
                Text(pendingText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if model.syncState == .syncing {
                ProgressView()
            } else {
                Button("Sync") {
                    Task { await model.syncNow() }
                }
            }
        }
    }

    @ViewBuilder
    private func trailingActions(for document: DocumentRecord) -> some View {
        switch model.libraryView {
        case .active:
            Button("Archive") {
                Task { await model.archiveDocument(document.documentID, archived: true) }
            }
            .tint(.orange)
            Button("Trash", role: .destructive) {
                Task { await model.trashDocument(document.documentID) }
            }
        case .archived:
            Button("Unarchive") {
                Task { await model.archiveDocument(document.documentID, archived: false) }
            }
            Button("Trash", role: .destructive) {
                Task { await model.trashDocument(document.documentID) }
            }
        case .trash:
            Button("Restore") {
                Task { await model.restoreDocument(document.documentID) }
            }
        }
    }

    private var sectionTitle: String {
        switch model.libraryView {
        case .active: return "Notebooks"
        case .archived: return "Archived"
        case .trash: return "Trash"
        }
    }

    private var emptyText: String {
        switch model.libraryView {
        case .active: return "No notebooks on this device yet. Create one to start writing with Apple Pencil."
        case .archived: return "No archived notebooks."
        case .trash: return "Trash is empty."
        }
    }

    private var syncTitle: String {
        switch model.syncState {
        case .idle:
            return "Local only"
        case .syncing:
            return "Syncing…"
        case let .online(date):
            return "Online · last sync \(date.formatted(date: .omitted, time: .shortened))"
        case let .offline(reason):
            return "Offline · \(reason)"
        case let .failed(reason):
            return "Sync failed · \(reason)"
        }
    }

    private var pendingText: String {
        if model.pendingCount == 0 {
            return "No pending operations"
        }
        return "\(model.pendingCount) pending operation\(model.pendingCount == 1 ? "" : "s") on device"
    }

    private func subtitle(for document: DocumentRecord) -> String {
        let updated = document.updatedAt.formatted(date: .abbreviated, time: .shortened)
        if document.trashedAt != nil {
            return "Trashed · \(updated)"
        }
        if document.archivedAt != nil {
            return "Archived · \(updated)"
        }
        return "Updated \(updated) · v\(document.syncVersion)"
    }
}

private extension DocumentLibraryView {
    var label: String {
        switch self {
        case .active: return "Active"
        case .archived: return "Archive"
        case .trash: return "Trash"
        }
    }
}
