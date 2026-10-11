import PersonalizedNotesKit
import SwiftUI
import UniformTypeIdentifiers

#if os(iOS) && canImport(PhotosUI)
import PhotosUI
#endif

struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var newTitle = ""
    @State private var showingCreate = false
    @State private var showingRename = false
    @State private var renameDocumentID: UUID?
    @State private var renameTitle = ""
    @State private var organizationEditor: LibraryOrganizationEditor?
    @State private var showingAccount = false
    @State private var showingSettings = false
    @State private var showingGettingStarted = false
    @State private var showingImporter = false
    @State private var showingSyncIssues = false
    @State private var layout = LibraryLayout.load()
    @State private var sort = LibrarySort.load()
    @State private var selectedDocumentIDs: Set<UUID> = []
    @State private var pendingPermanentDeleteIDs: Set<UUID> = []
    #if os(iOS) && canImport(PhotosUI)
    @State private var selectedPhotos: [PhotosPickerItem] = []
    @State private var showingPhotoPicker = false
    #endif

    var body: some View {
        List(selection: $selectedDocumentIDs) {
            Section {
                syncStatusRow
            }
            if model.libraryView == .trash {
                Section {
                    Label(
                        "Trash is retained on this iPad until you explicitly delete it permanently. Permanent deletion is account-wide after the server accepts it.",
                        systemImage: "clock.arrow.circlepath"
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
            }
            Section(sectionTitle) {
                if model.documents.isEmpty {
                    if model.libraryView == .active {
                        EmptyLibraryStartView {
                            showingCreate = true
                        } onGettingStarted: {
                            showingGettingStarted = true
                        }
                    } else {
                        Text(emptyText)
                            .foregroundStyle(.secondary)
                    }
                } else if layout == .grid {
                    LazyVGrid(
                        columns: [GridItem(.adaptive(minimum: 180), spacing: 14)],
                        spacing: 14
                    ) {
                        ForEach(sortedDocuments) { document in
                            gridCard(for: document)
                                .tag(document.documentID)
                        }
                        if model.hasMoreDocuments {
                            Button("Load more notebooks") {
                                Task { await model.loadMoreLibrary() }
                            }
                            .buttonStyle(.bordered)
                            .frame(maxWidth: .infinity)
                        }
                    }
                    .padding(.vertical, 6)
                } else {
                    ForEach(sortedDocuments) { document in
                        Button {
                            model.openNotebook(
                                document.documentID,
                                pageID: matchingPage(for: document)?.pageID
                            )
                        } label: {
                            HStack(spacing: 10) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(document.title)
                                        .foregroundStyle(.primary)
                                    Text(subtitle(for: document))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    if let page = matchingPage(for: document) {
                                        Text("Page match: \(pageDisplayName(for: page))")
                                            .font(.caption2)
                                            .foregroundStyle(.tint)
                                    }
                                }
                                Spacer(minLength: 8)
                                if document.isFavorite {
                                    Image(systemName: "star.fill")
                                        .foregroundStyle(.yellow)
                                        .accessibilityLabel("Favorite")
                                }
                            }
                        }
                        .contextMenu { notebookContextMenu(for: document) }
                        .accessibilityLabel(document.title)
                        .accessibilityHint("Opens notebook")
                        .tag(document.documentID)
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            trailingActions(for: document)
                        }
                    }
                    if model.hasMoreDocuments {
                        Button("Load more notebooks") {
                            Task { await model.loadMoreLibrary() }
                        }
                        .frame(maxWidth: .infinity)
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
            selectedDocumentIDs.removeAll()
            Task { await model.refreshLibrary() }
        }
        .onChange(of: layout) { _, value in value.save() }
        .onChange(of: sort) { _, value in value.save() }
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
                Menu {
                    Button {
                        showingCreate = true
                    } label: {
                        Label("New Notebook", systemImage: "square.and.pencil")
                    }
                    .keyboardShortcut("n", modifiers: [.command])
                    Button {
                        showingImporter = true
                    } label: {
                        Label("Import PDF, Image, or Notebook", systemImage: "square.and.arrow.down")
                    }
                    .keyboardShortcut("o", modifiers: [.command])
                    #if os(iOS) && canImport(PhotosUI)
                    Button {
                        showingPhotoPicker = true
                    } label: {
                        Label("Import Photos", systemImage: "photo.on.rectangle.angled")
                    }
                    #endif
                } label: {
                    Label("New", systemImage: "plus")
                }
                .disabled(model.libraryView != .active)
            }
            ToolbarItem(placement: .automatic) {
                Menu {
                    Picker("Layout", selection: $layout) {
                        ForEach(LibraryLayout.allCases, id: \.self) { option in
                            Label(option.title, systemImage: option.systemImage)
                                .tag(option)
                        }
                    }
                    Picker("Sort", selection: $sort) {
                        ForEach(LibrarySort.allCases, id: \.self) { option in
                            Text(option.title).tag(option)
                        }
                    }
                } label: {
                    Label("Library view options", systemImage: layout.systemImage)
                }
                .accessibilityLabel("Library view options")
            }
            #if os(iOS)
            ToolbarItem(placement: .automatic) {
                EditButton()
            }
            #endif
            if !selectedDocumentIDs.isEmpty {
                ToolbarItem(placement: .automatic) {
                    Menu {
                        if model.libraryView == .trash {
                            Button("Restore selected") {
                                let ids = selectedDocumentIDs
                                selectedDocumentIDs.removeAll()
                                Task {
                                    for id in ids { await model.restoreDocument(id) }
                                }
                            }
                            Button("Delete permanently", role: .destructive) {
                                pendingPermanentDeleteIDs = selectedDocumentIDs
                                selectedDocumentIDs.removeAll()
                            }
                        } else {
                            Button("Archive selected") {
                                let ids = selectedDocumentIDs
                                selectedDocumentIDs.removeAll()
                                Task { await model.archiveDocuments(Array(ids), archived: true) }
                            }
                            Button("Add selected to Favorites") {
                                let ids = selectedDocumentIDs
                                selectedDocumentIDs.removeAll()
                                Task { await model.toggleFavorites(Array(ids)) }
                            }
                            Button("Trash selected", role: .destructive) {
                                let ids = selectedDocumentIDs
                                selectedDocumentIDs.removeAll()
                                Task { await model.trashDocuments(Array(ids)) }
                            }
                        }
                    } label: {
                        Label("Bulk actions", systemImage: "checklist")
                    }
                }
            }
            ToolbarItem(placement: leadingPlacement) {
                Menu {
                    if model.isGuest {
                        Button("Sign In or Create Account") { model.route = .welcome }
                    } else {
                        Button("Account") { showingAccount = true }
                    }
                    Button("Getting Started") { showingGettingStarted = true }
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
        .dropDestination(for: URL.self) { urls, _ in
            guard model.libraryView == .active, !urls.isEmpty else { return false }
            model.beginImport(.success(urls))
            return true
        }
        .fileImporter(
            isPresented: $showingImporter,
            allowedContentTypes: [.pdf, .image, .personalizedNotesNotebook],
            allowsMultipleSelection: true
        ) { result in
            model.beginImport(result)
        }
        #if os(iOS) && canImport(PhotosUI)
        .photosPicker(
            isPresented: $showingPhotoPicker,
            selection: $selectedPhotos,
            maxSelectionCount: 20,
            matching: .images
        )
        .onChange(of: selectedPhotos) { _, items in
            guard !items.isEmpty else { return }
            let selected = items
            selectedPhotos = []
            Task { await model.importPhotos(selected) }
        }
        #endif
        .alert("New Notebook", isPresented: $showingCreate) {
            TextField("Title", text: $newTitle)
            Button("Create") {
                let title = newTitle
                newTitle = ""
                Task { await model.createNotebook(title: title) }
            }
            Button("Cancel", role: .cancel) { newTitle = "" }
        }
        .alert("Rename Notebook", isPresented: $showingRename) {
            TextField("Title", text: $renameTitle)
            Button("Save") {
                guard let renameDocumentID else { return }
                let title = renameTitle
                self.renameDocumentID = nil
                renameTitle = ""
                Task { await model.renameDocument(renameDocumentID, title: title) }
            }
            Button("Cancel", role: .cancel) {
                renameDocumentID = nil
                renameTitle = ""
            }
        }
        .sheet(isPresented: $showingAccount) {
            NavigationStack { AccountView() }
        }
        .sheet(isPresented: $showingSettings) {
            NavigationStack { SettingsView() }
        }
        .sheet(isPresented: $showingGettingStarted) {
            NavigationStack { GettingStartedView() }
        }
        .sheet(isPresented: $showingSyncIssues) {
            NavigationStack { SyncIssuesView() }
        }
        .sheet(item: $organizationEditor) { editor in
            LibraryOrganizationEditorView(editor: editor) { value in
                switch editor.kind {
                case .tags:
                    Task { await model.updateTags(editor.documentID, rawValue: value) }
                case .folder:
                    Task { await model.moveToFolder(editor.documentID, rawValue: value) }
                }
            }
        }
        .alert(
            "Sync local notes to this account?",
            isPresented: Binding(
                get: { model.pendingGuestMergeCount > 0 },
                set: { if !$0 { model.deferGuestMerge() } }
            )
        ) {
            Button("Sync Local Notes") {
                Task { await model.acceptGuestMerge() }
            }
            Button("Keep on This iPad for Now", role: .cancel) {
                model.deferGuestMerge()
            }
        } message: {
            Text(
                "\(model.pendingGuestMergeCount) local note"
                    + (model.pendingGuestMergeCount == 1 ? " is" : "s are")
                    + " ready to attach using their existing document and page identities. Nothing is uploaded until you choose Sync Local Notes."
            )
        }
        .alert(
            "Delete notebook permanently?",
            isPresented: Binding(
                get: { !pendingPermanentDeleteIDs.isEmpty },
                set: { if !$0 { pendingPermanentDeleteIDs.removeAll() } }
            )
        ) {
            Button("Delete Permanently", role: .destructive) {
                let ids = pendingPermanentDeleteIDs
                pendingPermanentDeleteIDs.removeAll()
                Task { await model.permanentlyDeleteDocuments(Array(ids)) }
            }
            Button("Cancel", role: .cancel) {
                pendingPermanentDeleteIDs.removeAll()
            }
        } message: {
            Text(
                "This permanently removes \(pendingPermanentDeleteIDs.count == 1 ? "this notebook" : "these notebooks") from the account after sync. The action cannot be undone."
            )
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
                Text(model.isImporting ? importTitle : syncTitle)
                if let progress = model.importProgress {
                    ProgressView(value: Double(progress.processed), total: Double(progress.total))
                        .progressViewStyle(.linear)
                        .frame(maxWidth: 260)
                    Text(progress.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else {
                    Text(pendingText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            if model.isImporting {
                Button("Cancel") { model.cancelImport() }
                .buttonStyle(.bordered)
                .accessibilityHint("Stops after the current import and keeps any durable notebook already created")
            } else if model.syncState == .syncing {
                ProgressView()
            } else if !model.failedOperations.isEmpty || !model.syncConflicts.isEmpty || !model.saveFailures.isEmpty || !model.importFailures.isEmpty {
                Button("Review") { showingSyncIssues = true }
            } else if model.isGuest {
                Button("Sign In") { model.route = .welcome }
            } else {
                Button("Sync") {
                    Task { await model.syncNow() }
                }
            }
        }
    }

    private var importTitle: String {
        guard let progress = model.importProgress else { return "Importing…" }
        return "Importing \(progress.processed) of \(progress.total)"
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
        case .favorites:
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
            Button("Delete Permanently", role: .destructive) {
                pendingPermanentDeleteIDs = [document.documentID]
            }
        case .recent, .imported, .unsynced, .needsAttention:
            Button("Archive") {
                Task { await model.archiveDocument(document.documentID, archived: true) }
            }
            .tint(.orange)
            Button("Trash", role: .destructive) {
                Task { await model.trashDocument(document.documentID) }
            }
        }
    }

    private var sortedDocuments: [DocumentRecord] {
        switch sort {
        case .updated:
            return model.documents.sorted { $0.updatedAt > $1.updatedAt }
        case .created:
            return model.documents.sorted { $0.createdAt > $1.createdAt }
        case .title:
            return model.documents.sorted {
                $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
            }
        }
    }

    private func gridCard(for document: DocumentRecord) -> some View {
        Button {
            model.openNotebook(
                document.documentID,
                pageID: matchingPage(for: document)?.pageID
            )
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color.secondary.opacity(0.12))
                    if let page = document.pages.sorted(by: { $0.pageIndex < $1.pageIndex }).first {
                        PageThumbnailView(page: page)
                            .padding(10)
                    } else {
                        Image(systemName: "note.text")
                            .font(.largeTitle)
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(height: 150)
                Text(document.title)
                    .font(.headline)
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                if document.isFavorite {
                    Label("Favorite", systemImage: "star.fill")
                        .font(.caption)
                        .foregroundStyle(.yellow)
                }
                Text(subtitle(for: document))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                if let page = matchingPage(for: document) {
                    Text("Page match: \(pageDisplayName(for: page))")
                        .font(.caption2)
                        .foregroundStyle(.tint)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .contextMenu { notebookContextMenu(for: document) }
        #if os(iOS)
        .hoverEffect(.highlight)
        #endif
        .accessibilityLabel("\(document.title). \(subtitle(for: document))")
    }

    @ViewBuilder
    private func notebookContextMenu(for document: DocumentRecord) -> some View {
        Button("Rename", systemImage: "pencil") {
            renameDocumentID = document.documentID
            renameTitle = document.title
            showingRename = true
        }
        Button("Open", systemImage: "arrow.up.right") {
            model.openNotebook(
                document.documentID,
                pageID: matchingPage(for: document)?.pageID
            )
        }
        Button("Duplicate", systemImage: "plus.square.on.square") {
            Task { await model.duplicateNotebook(document.documentID) }
        }
        Button(
            document.isFavorite ? "Remove from Favorites" : "Add to Favorites",
            systemImage: document.isFavorite ? "star.slash" : "star"
        ) {
            Task { await model.toggleFavorite(document.documentID) }
        }
        Button("Edit Tags", systemImage: "tag") {
            organizationEditor = LibraryOrganizationEditor(
                documentID: document.documentID,
                kind: .tags,
                initialValue: document.organizationTags.joined(separator: ", ")
            )
        }
        Button("Move to Folder", systemImage: "folder") {
            organizationEditor = LibraryOrganizationEditor(
                documentID: document.documentID,
                kind: .folder,
                initialValue: document.organizationFolderID ?? ""
            )
        }
        Divider()
        trailingActions(for: document)
    }

    private var sectionTitle: String {
        switch model.libraryView {
        case .active: return "Notebooks"
        case .favorites: return "Favorites"
        case .archived: return "Archived"
        case .trash: return "Trash"
        case .recent: return "Recent"
        case .imported: return "Imported"
        case .unsynced: return "Unsynced"
        case .needsAttention: return "Needs attention"
        }
    }

    private var emptyText: String {
        switch model.libraryView {
        case .active: return "No notebooks on this device yet. Create one to start writing with Apple Pencil."
        case .favorites: return "No favorite notebooks yet. Add a favorite from a notebook's actions."
        case .archived: return "No archived notebooks."
        case .trash: return "Trash is empty. Items stay here until you explicitly delete them permanently."
        case .recent: return "No notebooks updated in the last 7 days."
        case .imported: return "No imported notebooks yet."
        case .unsynced: return "All notebooks are synced."
        case .needsAttention: return "No notebooks need attention."
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
        if !model.failedOperations.isEmpty || !model.syncConflicts.isEmpty || !model.saveFailures.isEmpty || !model.importFailures.isEmpty {
            let failures = model.failedOperations.count
            let conflicts = model.syncConflicts.count
            let saves = model.saveFailures.count
            let imports = model.importFailures.count
            var parts: [String] = []
            if failures > 0 { parts.append("\(failures) failed") }
            if conflicts > 0 { parts.append("\(conflicts) conflict\(conflicts == 1 ? "" : "s")") }
            if saves > 0 { parts.append("\(saves) local save\(saves == 1 ? "" : "s")") }
            if imports > 0 { parts.append("\(imports) import\(imports == 1 ? "" : "s")") }
            return parts.joined(separator: " · ") + " · review required"
        }
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
        let tags = document.organizationTags.map { "#\($0)" }.joined(separator: " ")
        let base = "Updated \(updated) · v\(document.syncVersion)"
        return tags.isEmpty ? base : "\(base) · \(tags)"
    }

    private func matchingPage(for document: DocumentRecord) -> PageSummary? {
        let query = model.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return nil }
        return document.pages
            .sorted { $0.pageIndex < $1.pageIndex }
            .first { PageNavigatorFilter.matches($0, query: query) }
    }

    private func pageDisplayName(for page: PageSummary) -> String {
        let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Page \(page.pageIndex + 1)" : title
    }
}

private struct EmptyLibraryStartView: View {
    let onCreate: () -> Void
    let onGettingStarted: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Start with a local notebook", systemImage: "note.text.badge.plus")
                .font(.headline)
            Text("Create a notebook, write with Apple Pencil, and keep working even without a connection. Your local save happens immediately; sign in later when you want sync and backup.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Button("Create Notebook", action: onCreate)
                .buttonStyle(.borderedProminent)
                .accessibilityHint("Creates a notebook saved on this iPad")
            Button("Getting Started", action: onGettingStarted)
                .buttonStyle(.bordered)
                .accessibilityHint("Explains the first writing, page, and sync steps")
        }
        .padding(.vertical, 12)
        .accessibilityElement(children: .contain)
    }
}

private struct LibraryOrganizationEditor: Identifiable {
    enum Kind: Equatable {
        case tags
        case folder
    }

    let id = UUID()
    let documentID: UUID
    let kind: Kind
    let initialValue: String
}

private struct LibraryOrganizationEditorView: View {
    @Environment(\.dismiss) private var dismiss
    let editor: LibraryOrganizationEditor
    let onSave: (String) -> Void
    @State private var value: String

    init(
        editor: LibraryOrganizationEditor,
        onSave: @escaping (String) -> Void
    ) {
        self.editor = editor
        self.onSave = onSave
        _value = State(initialValue: editor.initialValue)
    }

    var body: some View {
        NavigationStack {
            Form {
                #if os(iOS)
                TextField(editor.kind == .tags ? "Tags separated by commas" : "Folder path (e.g. School/Math)", text: $value)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                #else
                TextField(editor.kind == .tags ? "Tags separated by commas" : "Folder path (e.g. School/Math)", text: $value)
                #endif
                Text(editor.kind == .tags
                    ? "Tags sync as notebook metadata. Duplicate tags are removed."
                    : "Use up to four levels separated by /. Leave empty for the root.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .navigationTitle(editor.kind == .tags ? "Edit Tags" : "Move to Folder")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onSave(value)
                        dismiss()
                    }
                }
            }
        }
    }
}

extension UTType {
    static var personalizedNotesNotebook: UTType {
        UTType(exportedAs: "com.handwrittennotes.notebook", conformingTo: .data)
    }
}

struct SyncIssuesView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            if !model.importFailures.isEmpty {
                Section("Imports needing attention") {
                    ForEach(model.importFailures) { failure in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(failure.originalFilename)
                                .font(.headline)
                            Text(failure.reason)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                            HStack {
                                Button("Retry import") {
                                    model.retryImport(failure)
                                }
                                .buttonStyle(.borderedProminent)
                                Button("Discard", role: .destructive) {
                                    model.discardImportFailure(failure)
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            if !model.saveFailures.isEmpty {
                Section("Local save failures") {
                    ForEach(model.saveFailures) { failure in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Page \(failure.pageID.uuidString.prefix(8))")
                                .font(.headline)
                            Text(failure.error)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                            Button("Retry save") {
                                Task { await model.retryLocalSave(pageID: failure.pageID) }
                            }
                            .buttonStyle(.borderedProminent)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            if !model.failedOperations.isEmpty {
                Section("Retryable sync failures") {
                    ForEach(model.failedOperations) { operation in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(operation.operationType)
                                .font(.headline)
                            if let lastError = operation.lastError {
                                Text(lastError)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                            }
                            Button("Retry") {
                                Task { await model.retrySyncOperation(operation.clientOperationID) }
                            }
                            .buttonStyle(.bordered)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            if !model.syncConflicts.isEmpty {
                Section("Conflicts") {
                    ForEach(model.syncConflicts) { conflict in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(conflict.operationType)
                                .font(.headline)
                            Text("This change was made from an older notebook version. Choose which recoverable result to keep.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            HStack {
                                Button("Keep server") {
                                    Task { await model.resolveSyncConflict(conflict.conflictID, decision: .keepServer) }
                                }
                                .buttonStyle(.bordered)
                                Button("Retry my change") {
                                    Task { await model.resolveSyncConflict(conflict.conflictID, decision: .retryAsNew) }
                                }
                                .buttonStyle(.borderedProminent)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            if model.failedOperations.isEmpty && model.syncConflicts.isEmpty && model.saveFailures.isEmpty && model.importFailures.isEmpty {
                ContentUnavailableView(
                    "All synced",
                    systemImage: "checkmark.icloud",
                    description: Text("There are no failed operations or unresolved conflicts on this iPad.")
                )
            }
        }
        .navigationTitle("Sync issues")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
    }
}

private extension DocumentLibraryView {
    var label: String {
        switch self {
        case .active: return "Active"
        case .favorites: return "Favorites"
        case .archived: return "Archive"
        case .trash: return "Trash"
        case .recent: return "Recent"
        case .imported: return "Imported"
        case .unsynced: return "Unsynced"
        case .needsAttention: return "Needs attention"
        }
    }
}

private enum LibraryLayout: String, CaseIterable {
    case list
    case grid

    private static let defaultsKey = "personalized-notes.library-layout"

    static func load() -> LibraryLayout {
        guard let raw = UserDefaults.standard.string(forKey: defaultsKey),
              let value = LibraryLayout(rawValue: raw) else {
            return .list
        }
        return value
    }

    func save() {
        UserDefaults.standard.set(rawValue, forKey: Self.defaultsKey)
    }

    var title: String {
        switch self {
        case .list: return "List"
        case .grid: return "Grid"
        }
    }

    var systemImage: String {
        switch self {
        case .list: return "list.bullet"
        case .grid: return "square.grid.2x2"
        }
    }
}

private enum LibrarySort: String, CaseIterable {
    case updated
    case created
    case title

    private static let defaultsKey = "personalized-notes.library-sort"

    static func load() -> LibrarySort {
        guard let raw = UserDefaults.standard.string(forKey: defaultsKey),
              let value = LibrarySort(rawValue: raw) else {
            return .updated
        }
        return value
    }

    func save() {
        UserDefaults.standard.set(rawValue, forKey: Self.defaultsKey)
    }

    var title: String {
        switch self {
        case .updated: return "Recently updated"
        case .created: return "Recently created"
        case .title: return "Title"
        }
    }
}
