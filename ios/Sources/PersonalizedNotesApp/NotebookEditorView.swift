import PersonalizedNotesKit
import SwiftUI
import UniformTypeIdentifiers

#if canImport(UIKit)
import UIKit
#endif

#if canImport(PencilKit) && canImport(UIKit)
import PencilKit
#endif

#if canImport(PDFKit) && canImport(UIKit)
import PDFKit
#endif

#if os(iOS) && canImport(PhotosUI)
import PhotosUI
#endif

struct NotebookEditorView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let documentID: UUID
    let initialPageID: UUID?

    @State private var selectedPageID: UUID?
    @State private var allowsFingerDrawing = false
    @State private var canvasCommand: PencilCanvasCommand?
    @State private var showingOriginal = false
    @State private var showingConversionReview = false
    @State private var shareableExport: ShareableExportFile?
    @State private var printableExport: ShareableExportFile?
    @State private var fileExportDocument: LocalExportFileDocument?
    @State private var showingFileExporter = false
    @State private var fileExportContentType: UTType = .data
    @State private var fileExportFilename = "note"
    @State private var showingImportIntoNotebook = false
    @State private var showingImportAsNotebook = false
#if os(iOS) && canImport(PhotosUI)
    @State private var selectedPhotosForNotebook: [PhotosPickerItem] = []
    @State private var selectedPhotosAsNotebook: [PhotosPickerItem] = []
#endif
    @State private var pagePendingDeletion: UUID?
    @State private var showingPageRename = false
    @State private var pageRenameTitle = ""
    @State private var showingCustomPageSize = false

    init(documentID: UUID, initialPageID: UUID? = nil) {
        self.documentID = documentID
        self.initialPageID = initialPageID
        _selectedPageID = State(initialValue: initialPageID)
    }

    var body: some View {
        Group {
            if let document = model.document(id: documentID) {
                editor(for: document)
            } else {
                ContentUnavailableView(
                    "Notebook unavailable",
                    systemImage: "note.text",
                    description: Text("The local library no longer contains this notebook.")
                )
            }
        }
        .onAppear {
            if selectedPageID == nil || !sortedPages.contains(where: { $0.pageID == selectedPageID }) {
                selectedPageID = initialPageID.flatMap { pageID in
                    sortedPages.contains(where: { $0.pageID == pageID }) ? pageID : nil
                } ?? sortedPages.first?.pageID
            }
        }
        .onChange(of: documentID) { _, _ in
            selectedPageID = initialPageID.flatMap { pageID in
                sortedPages.contains(where: { $0.pageID == pageID }) ? pageID : nil
            } ?? sortedPages.first?.pageID
        }
#if os(iOS) && canImport(PhotosUI)
        .onChange(of: selectedPhotosForNotebook) { _, items in
            guard !items.isEmpty else { return }
            let selected = items
            selectedPhotosForNotebook = []
            Task {
                await model.importPhotos(selected, destination: .existingNotebook(documentID))
            }
        }
        .onChange(of: selectedPhotosAsNotebook) { _, items in
            guard !items.isEmpty else { return }
            let selected = items
            selectedPhotosAsNotebook = []
            Task {
                await model.importPhotos(selected, destination: .newNotebook)
            }
        }
#endif
        .onChange(of: scenePhase) { _, phase in
            guard phase != .active else { return }
            // Backgrounding is a persistence boundary. The representable
            // flushes the current PKDrawing without waiting for the network.
            canvasCommand = PencilCanvasCommand(action: .save)
        }
        #if canImport(UIKit)
        .onReceive(
            NotificationCenter.default.publisher(
                for: UIApplication.didReceiveMemoryWarningNotification
            )
        ) { _ in
            canvasCommand = PencilCanvasCommand(action: .save)
        }
        #endif
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Library") { model.closeNotebook() }
            }
        }
        .fileImporter(
            isPresented: $showingImportIntoNotebook,
            allowedContentTypes: [.pdf, .image, .personalizedNotesNotebook],
            allowsMultipleSelection: true
        ) { result in
            model.beginImport(result, destination: .existingNotebook(documentID))
        }
        .fileImporter(
            isPresented: $showingImportAsNotebook,
            allowedContentTypes: [.pdf, .image, .personalizedNotesNotebook],
            allowsMultipleSelection: true
        ) { result in
            model.beginImport(result, destination: .newNotebook)
        }
    }

    private func editor(for document: DocumentRecord) -> some View {
        let pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        let currentPage = pages.first(where: { $0.pageID == selectedPageID }) ?? pages.first

        return VStack(spacing: 0) {
            SaveSyncStatusView(document: document, pageID: currentPage?.pageID)
                .padding(.horizontal)
                .padding(.vertical, 8)
            if let currentPage,
               let conversionRun = model.conversionRuns[currentPage.pageID] {
                ConversionStatusView(conversionRun: conversionRun) {
                    Task {
                        await model.requestPageConversion(
                            documentID: documentID,
                            pageID: currentPage.pageID
                        )
                    }
                }
                .padding(.horizontal)
                .padding(.bottom, 8)
                if conversionRun.isSuccessful, !conversionRun.resultIDs.isEmpty {
                    Button("Review Conversion") { showingConversionReview = true }
                        .accessibilityIdentifier("review-conversion")
                        .padding(.bottom, 8)
                }
            }
            Divider()

            if let currentPage {
                let navigator = PageNavigatorView(
                    pages: pages,
                    selectedPageID: Binding(
                        get: { selectedPageID ?? currentPage.pageID },
                        set: { selectedPageID = $0 }
                    ),
                    onAdd: { Task { await model.addPage(to: documentID, after: currentPage.pageID) } },
                    onDuplicate: { Task { await model.duplicatePage(documentID: documentID, pageID: currentPage.pageID) } },
                    onDelete: { pagePendingDeletion = currentPage.pageID },
                    onMove: { offset in
                        Task { await model.movePage(documentID: documentID, pageID: currentPage.pageID, by: offset) }
                    },
                    compact: horizontalSizeClass == .compact
                )
                let canvas = editorCanvas(document: document, page: currentPage)
                if horizontalSizeClass == .compact {
                    VStack(spacing: 0) {
                        navigator
                        Divider()
                        canvas
                    }
                } else {
                    HStack(spacing: 0) {
                        navigator
                        Divider()
                        canvas
                    }
                }
            } else {
                ContentUnavailableView(
                    "No pages",
                    systemImage: "doc.badge.plus",
                    description: Text("Add a page to start writing.")
                )
            }
        }
        .navigationTitle(document.title)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    canvasCommand = PencilCanvasCommand(action: .undo)
                } label: {
                    Label("Undo", systemImage: "arrow.uturn.backward")
                }
                .keyboardShortcut("z", modifiers: .command)

                Button {
                    canvasCommand = PencilCanvasCommand(action: .redo)
                } label: {
                    Label("Redo", systemImage: "arrow.uturn.forward")
                }
                .keyboardShortcut("z", modifiers: [.command, .shift])

                Menu {
                    Button {
                        showingImportIntoNotebook = true
                    } label: {
                        Label("Import into This Notebook", systemImage: "rectangle.badge.plus")
                    }
                    Button {
                        showingImportAsNotebook = true
                    } label: {
                        Label("Import as New Notebook", systemImage: "square.and.arrow.down")
                    }
#if os(iOS) && canImport(PhotosUI)
                    PhotosPicker(
                        selection: $selectedPhotosForNotebook,
                        maxSelectionCount: 50,
                        matching: .images
                    ) {
                        Label("Import Photos into This Notebook", systemImage: "photo.on.rectangle.angled")
                    }
                    PhotosPicker(
                        selection: $selectedPhotosAsNotebook,
                        maxSelectionCount: 50,
                        matching: .images
                    ) {
                        Label("Import Photos as New Notebook", systemImage: "photo.stack")
                    }
#endif
                    Divider()
                    Section("Presets") {
                        ForEach(CanvasToolPresetStore.load()) { preset in
                            Button {
                                canvasCommand = PencilCanvasCommand(action: .selectPreset(preset))
                            } label: {
                                Label(preset.name, systemImage: preset.systemImage)
                            }
                        }
                    }
                    Divider()
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .selectTool(.pen))
                    } label: {
                        Label("Pen", systemImage: "pencil.line")
                    }
                    .keyboardShortcut("p", modifiers: [.command, .option])
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .selectTool(.pencil))
                    } label: {
                        Label("Pencil", systemImage: "pencil")
                    }
                    .keyboardShortcut("l", modifiers: [.command, .option])
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .selectTool(.marker))
                    } label: {
                        Label("Marker", systemImage: "highlighter")
                    }
                    .keyboardShortcut("m", modifiers: [.command, .option])
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .selectTool(.eraser))
                    } label: {
                        Label("Eraser", systemImage: "eraser")
                    }
                    .keyboardShortcut("e", modifiers: [.command, .option])
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .selectTool(.lasso))
                    } label: {
                        Label("Lasso", systemImage: "lasso")
                    }
                    Divider()
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .cutSelection)
                    } label: {
                        Label("Cut Selection", systemImage: "scissors")
                    }
                    .keyboardShortcut("x", modifiers: .command)
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .copySelection)
                    } label: {
                        Label("Copy Selection", systemImage: "doc.on.doc")
                    }
                    .keyboardShortcut("c", modifiers: .command)
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .pasteSelection)
                    } label: {
                        Label("Paste Selection", systemImage: "doc.on.clipboard")
                    }
                    .keyboardShortcut("v", modifiers: .command)
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .toggleRuler)
                    } label: {
                        Label("Toggle Ruler", systemImage: "ruler")
                    }
                } label: {
                    Label("Tools", systemImage: "pencil.and.outline")
                }

                Menu {
                    Button {
                        guard let currentPage else { return }
                        Task {
                            await model.requestPageConversion(
                                documentID: documentID,
                                pageID: currentPage.pageID
                            )
                        }
                    } label: {
                        Label("Convert Page", systemImage: "text.viewfinder")
                    }
                    .disabled(currentPage == nil || (currentPage.flatMap { model.conversionRuns[$0.pageID] }?.isTerminal == false))
                    Button {
                        guard let currentPage else { return }
                        Task { await model.requestPageConversion(documentID: documentID, pageID: currentPage.pageID, mode: "prose") }
                    } label: {
                        Label("Convert Prose", systemImage: "text.alignleft")
                    }
                    .disabled(currentPage == nil || (currentPage.flatMap { model.conversionRuns[$0.pageID] }?.isTerminal == false))
                    Button {
                        Task {
                            await model.requestNotebookConversion(documentID: documentID)
                        }
                    } label: {
                        Label("Convert Notebook", systemImage: "rectangle.stack.badge.play")
                    }
                    .disabled(model.notebookConversionProgress != nil)
                    if let progress = model.notebookConversionProgress,
                       progress.documentID == documentID {
                        Text(progress.summary)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Divider()
                    Button {
                        Task { await model.addPage(to: documentID, after: currentPage?.pageID) }
                    } label: {
                        Label("Add Page", systemImage: "plus")
                    }
                    .keyboardShortcut("n", modifiers: [.command, .option])
                    Button {
                        if let currentPage {
                            Task { await model.duplicatePage(documentID: documentID, pageID: currentPage.pageID) }
                        }
                    } label: {
                        Label("Duplicate Page", systemImage: "plus.square.on.square")
                    }
                    .disabled(currentPage == nil)
                    .keyboardShortcut("d", modifiers: [.command, .option])
                    Button {
                        if let currentPage {
                            Task { await model.movePage(documentID: documentID, pageID: currentPage.pageID, by: -1) }
                        }
                    } label: {
                        Label("Move Page Up", systemImage: "arrow.up")
                    }
                    .disabled(currentPage == nil || currentPageIndex == 0)
                    .keyboardShortcut(.upArrow, modifiers: [.command, .option])
                    Button {
                        if let currentPage {
                            Task { await model.movePage(documentID: documentID, pageID: currentPage.pageID, by: 1) }
                        }
                    } label: {
                        Label("Move Page Down", systemImage: "arrow.down")
                    }
                    .disabled(currentPage == nil || currentPageIndex == pages.count - 1)
                    .keyboardShortcut(.downArrow, modifiers: [.command, .option])
                    Button(role: .destructive) {
                        pagePendingDeletion = currentPage?.pageID
                    } label: {
                        Label("Delete Page", systemImage: "trash")
                    }
                    .disabled(currentPage == nil || pages.count <= 1)
                    Button {
                        pageRenameTitle = currentPage?.title ?? ""
                        showingPageRename = true
                    } label: {
                        Label("Rename Page", systemImage: "pencil.and.outline")
                    }
                    .disabled(currentPage == nil)
                    Menu {
                        ForEach(PagePaperTemplate.allCases, id: \.self) { template in
                            Button {
                                guard let currentPage else { return }
                                Task {
                                    await model.setPageTemplate(
                                        documentID: documentID,
                                        pageID: currentPage.pageID,
                                        template: template
                                    )
                                }
                            } label: {
                                if currentPage?.paperTemplate == template.rawValue {
                                    Label(template.title, systemImage: "checkmark")
                                } else {
                                    Text(template.title)
                                }
                            }
                        }
                    } label: {
                        Label("Paper Template", systemImage: "rectangle.and.pencil.and.ellipsis")
                    }
                    .disabled(currentPage == nil)
                    Menu {
                        ForEach(PageSizePreset.allCases) { preset in
                            Button(preset.title) {
                                guard let currentPage else { return }
                                Task {
                                    await model.resizePage(
                                        documentID: documentID,
                                        pageID: currentPage.pageID,
                                        width: preset.width,
                                        height: preset.height
                                    )
                                }
                            }
                        }
                        Divider()
                        Button("Custom size…") {
                            showingCustomPageSize = true
                        }
                    } label: {
                        Label("Page Size", systemImage: "rectangle.resize")
                    }
                    .disabled(currentPage == nil)
                    Button {
                        canvasCommand = PencilCanvasCommand(action: .resetZoom)
                    } label: {
                        Label("Fit Page", systemImage: "arrow.up.left.and.arrow.down.right")
                    }
                    .keyboardShortcut("0", modifiers: [.command])
                    if hasSource(for: document, page: currentPage) {
                        Button {
                            showingOriginal = true
                        } label: {
                            Label("Show Original", systemImage: "doc.richtext")
                        }
                    }
                    Button {
                        guard let currentPage else { return }
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalPDFExporter.export(document: document, page: currentPage)
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Page as PDF", systemImage: "square.and.arrow.up")
                    }
                    .keyboardShortcut("e", modifiers: [.command, .shift])
                    Button {
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalPDFExporter.export(document: document)
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Notebook as PDF", systemImage: "doc.richtext")
                    }
                    Button {
                        do {
                            let url = try LocalPDFExporter.export(document: document)
                            presentFileExporter(url: url, contentType: .pdf)
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Save Notebook PDF to Files", systemImage: "folder")
                    }
                    .keyboardShortcut("s", modifiers: [.command, .shift])
                    Button {
                        do {
                            printableExport = ShareableExportFile(
                                url: try LocalPDFExporter.export(document: document)
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Print Notebook", systemImage: "printer")
                    }
                    Button {
                        guard let currentPage else { return }
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalPDFExporter.exportPageImage(
                                    document: document,
                                    page: currentPage,
                                    format: .png
                                )
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Page as PNG", systemImage: "photo")
                    }
                    Button {
                        guard let currentPage else { return }
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalPDFExporter.exportPageImage(
                                    document: document,
                                    page: currentPage,
                                    format: .jpeg
                                )
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Page as JPEG", systemImage: "photo")
                    }
                    Button {
                        guard let currentPage else { return }
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalCanonicalInkExporter.export(
                                    page: currentPage,
                                    documentTitle: document.title
                                )
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Page as ink_v1", systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                    }
                    Button {
                        do {
                            shareableExport = ShareableExportFile(
                                url: try LocalNativeArchiveExporter.export(document: document)
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Export Native Notebook Archive", systemImage: "archivebox")
                    }
                    Button {
                        do {
                            let url = try LocalNativeArchiveExporter.export(document: document)
                            presentFileExporter(
                                url: url,
                                contentType: UTType(exportedAs: "com.handwrittennotes.notebook", conformingTo: .data)
                            )
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    } label: {
                        Label("Save Native Archive to Files", systemImage: "folder.badge.plus")
                    }
                    if documentHasImportedSource(document) {
                        Button {
                            do {
                                shareableExport = ShareableExportFile(
                                    url: try LocalSourceExporter.exportOriginal(document: document)
                                )
                            } catch {
                                model.errorMessage = error.localizedDescription
                            }
                        } label: {
                            Label("Export Original Source", systemImage: "doc.on.doc")
                        }
                    }
                    Divider()
                    Toggle(isOn: $allowsFingerDrawing) {
                        Label("Draw with Finger", systemImage: "hand.draw")
                    }
                } label: {
                    Label("Editor actions", systemImage: "ellipsis.circle")
                }
            }
        }
        .onChange(of: pages.map(\.pageID)) { _, pageIDs in
            if let selectedPageID, pageIDs.contains(selectedPageID) { return }
            self.selectedPageID = pageIDs.first
        }
        .alert("Delete Page?", isPresented: Binding(
            get: { pagePendingDeletion != nil },
            set: { if !$0 { pagePendingDeletion = nil } }
        )) {
            Button("Delete", role: .destructive) {
                guard let pageID = pagePendingDeletion else { return }
                pagePendingDeletion = nil
                Task { await model.deletePage(documentID: documentID, pageID: pageID) }
            }
            Button("Cancel", role: .cancel) { pagePendingDeletion = nil }
        } message: {
            Text("This removes the page from the notebook. Your other pages and their ink remain unchanged.")
        }
        .alert("Rename Page", isPresented: $showingPageRename) {
            TextField("Page title", text: $pageRenameTitle)
            Button("Save") {
                guard let pageID = selectedPageID else { return }
                Task {
                    await model.renamePage(
                        documentID: documentID,
                        pageID: pageID,
                        title: pageRenameTitle.isEmpty ? nil : pageRenameTitle
                    )
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Use a short, descriptive name, or clear the field to return to the numbered page label.")
        }
        .sheet(isPresented: $showingOriginal) {
            NavigationStack {
                if let currentPage {
                    ImportedSourcePreviewView(document: document, page: currentPage)
                } else {
                    ContentUnavailableView("Original unavailable", systemImage: "doc.slash")
                }
            }
        }
        .sheet(isPresented: $showingConversionReview) {
            if let pageID = selectedPageID, let run = model.conversionRuns[pageID] {
                ConversionReviewView(run: run)
            }
        }
        .sheet(isPresented: $showingCustomPageSize) {
            if let document = model.document(id: documentID),
               let page = document.pages.first(where: { $0.pageID == selectedPageID }) ?? document.pages.first {
                PageSizeEditorView(initialWidth: page.width, initialHeight: page.height) { width, height in
                    Task {
                        await model.resizePage(
                            documentID: documentID,
                            pageID: page.pageID,
                            width: width,
                            height: height
                        )
                    }
                }
            } else {
                ContentUnavailableView("Page unavailable", systemImage: "rectangle.slash")
            }
        }
        .sheet(item: $shareableExport) { file in
            #if canImport(UIKit)
            ActivityShareView(file: file)
            #else
            Text(file.url.path)
            #endif
        }
        #if os(iOS) && canImport(UIKit)
        .sheet(item: $printableExport) { file in
            PrintInteractionView(pdfURL: file.url) { error in
                printableExport = nil
                if let error {
                    model.errorMessage = error.localizedDescription
                }
            }
        }
        #endif
        .fileExporter(
            isPresented: $showingFileExporter,
            document: fileExportDocument,
            contentType: fileExportContentType,
            defaultFilename: fileExportFilename
        ) { result in
            if case let .failure(error) = result {
                model.errorMessage = error.localizedDescription
            }
            fileExportDocument = nil
        }
    }

    @ViewBuilder
    private func editorCanvas(document: DocumentRecord, page: PageSummary) -> some View {
        ZStack {
            #if canImport(UIKit)
            Color(uiColor: .secondarySystemBackground)
            #else
            Color.gray.opacity(0.1)
            #endif
            #if canImport(PencilKit) && canImport(UIKit)
            PencilCanvasRepresentable(
                pageID: page.pageID,
                initialDrawingData: LocalInkCache.readPKDrawing(pageID: page.pageID),
                drawingRevisionID: LocalInkCache.revisionMetadata(pageID: page.pageID)?.revisionID,
                canvasSize: CGSize(
                    width: CGFloat(max(page.width, 1)),
                    height: CGFloat(max(page.height, 1))
                ),
                allowsFingerDrawing: allowsFingerDrawing,
                backgroundSourceURL: sourceDescriptor(for: document, page: page)?.url,
                backgroundSourceKind: sourceDescriptor(for: document, page: page)?.kind,
                backgroundPageIndex: sourceDescriptor(for: document, page: page)?.sourcePageIndex ?? page.pageIndex,
                paperTemplate: page.paperTemplate,
                command: canvasCommand,
                onDrawingCommit: { pageID, data in
                    model.commitDrawingLocally(
                        pageID: pageID,
                        pkDrawingData: data,
                        width: Double(page.width),
                        height: Double(page.height)
                    )
                }
            )
            .padding(16)
            #else
            CanvasUnavailableView()
            #endif
        }
        .accessibilityLabel(canvasAccessibilityLabel(for: page))
        .accessibilityHint("Use Apple Pencil to write. Use the page controls for non-canvas navigation.")
    }

    private var sortedPages: [PageSummary] {
        model.document(id: documentID)?.pages.sorted { $0.pageIndex < $1.pageIndex } ?? []
    }

    private var currentPageIndex: Int {
        guard let selectedPageID else { return 0 }
        return sortedPages.firstIndex(where: { $0.pageID == selectedPageID }) ?? 0
    }

    private func pageDisplayName(for page: PageSummary) -> String {
        let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Page \(page.pageIndex + 1)" : title
    }

    private func canvasAccessibilityLabel(for page: PageSummary) -> String {
        let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty
            ? "Writing canvas for page \(page.pageIndex + 1)"
            : "Writing canvas for \(title)"
    }

    private func sourceDescriptor(
        for document: DocumentRecord,
        page: PageSummary
    ) -> PageSourceDescriptor? {
        PageSourceMetadata.descriptor(for: document, page: page)
    }

    private func hasSource(for document: DocumentRecord, page: PageSummary?) -> Bool {
        guard let page else { return false }
        return sourceDescriptor(for: document, page: page) != nil
    }

    private func documentHasImportedSource(_ document: DocumentRecord) -> Bool {
        document.pages.contains { page in
            sourceDescriptor(for: document, page: page) != nil
        }
    }

    private func presentFileExporter(url: URL, contentType: UTType) {
        do {
            fileExportDocument = try LocalExportFileDocument(url: url)
            fileExportContentType = contentType
            fileExportFilename = url.deletingPathExtension().lastPathComponent
            showingFileExporter = true
        } catch {
            model.errorMessage = error.localizedDescription
        }
    }
}

struct ConversionStatusView: View {
    let conversionRun: RecognitionRun
    let onRetry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.callout.weight(.medium))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if conversionRun.state.lowercased() == "failed" {
                Button("Retry", action: onRetry)
                    .font(.callout.weight(.medium))
            }
        }
        .padding(10)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Conversion: \(title). \(detail)")
    }

    private var normalizedState: String {
        conversionRun.state.lowercased()
    }

    private var title: String {
        switch normalizedState {
        case "queued": return "Conversion queued"
        case "running", "processing": return "Converting page…"
        case "partial": return "Conversion partially complete"
        case "complete", "completed": return "Conversion complete"
        case "cancelled", "canceled": return "Conversion cancelled"
        case "failed": return "Conversion unavailable"
        default: return "Conversion \(conversionRun.state)"
        }
    }

    private var detail: String {
        if normalizedState == "failed" {
            if conversionRun.errorMessage == "model_unavailable" {
                return "The handwriting model is not configured on the server. Your original ink is safe."
            }
            return conversionRun.errorMessage?.replacingOccurrences(of: "_", with: " ")
                ?? "The server could not convert this page. Your original ink is safe."
        }
        if conversionRun.isSuccessful {
            let count = conversionRun.resultIDs.count
            let suffix = count == 1 ? "" : "s"
            return count == 0
                ? "The server returned no review blocks yet. Your original ink is unchanged."
                : "\(count) review block\(suffix) returned. Your original ink is unchanged."
        }
        return "Your original ink is unchanged while the server processes this page."
    }

    private var symbol: String {
        switch normalizedState {
        case "complete", "completed": return "checkmark.circle.fill"
        case "failed": return "exclamationmark.triangle.fill"
        case "cancelled", "canceled": return "xmark.circle"
        case "running", "processing": return "arrow.triangle.2.circlepath"
        default: return "clock"
        }
    }

    private var color: Color {
        switch normalizedState {
        case "complete", "completed": return .green
        case "failed": return .orange
        case "cancelled", "canceled": return .secondary
        default: return .accentColor
        }
    }
}

struct PageNavigatorView: View {
    let pages: [PageSummary]
    @Binding var selectedPageID: UUID
    let onAdd: () -> Void
    let onDuplicate: () -> Void
    let onDelete: () -> Void
    let onMove: (Int) -> Void
    let compact: Bool
    @State private var pageFilter = ""

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text("Pages")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                HStack(spacing: 2) {
                    Button(action: { onMove(-1) }) {
                        Image(systemName: "arrow.up")
                    }
                    .accessibilityLabel("Move page up")
                    .disabled(pages.isEmpty || selectedIndex == 0)
                    Button(action: { onMove(1) }) {
                        Image(systemName: "arrow.down")
                    }
                    .accessibilityLabel("Move page down")
                    .disabled(pages.isEmpty || selectedIndex == pages.count - 1)
                    Button(role: .destructive, action: onDelete) {
                        Image(systemName: "trash")
                    }
                    .accessibilityLabel("Delete page")
                    .disabled(pages.count <= 1)
                    Button(action: onAdd) {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("Add page")
                }
            }
            .padding(.horizontal, 10)

            if pages.count > 6 || !pageFilter.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                    TextField("Find pages", text: $pageFilter)
                        .textFieldStyle(.roundedBorder)
                        .accessibilityLabel("Find pages by title or number")
                    if !pageFilter.isEmpty {
                        Button("Clear") { pageFilter = "" }
                            .font(.caption)
                            .accessibilityLabel("Clear page search")
                    }
                }
                .padding(.horizontal, 10)
            }

            if compact {
                ScrollView(.horizontal) {
                    LazyHStack(spacing: 10) {
                        ForEach(filteredPages, content: pageButton)
                    }
                    .padding(.horizontal, 8)
                }
                .frame(maxHeight: 132)
            } else {
                ScrollView(.vertical) {
                    LazyVStack(spacing: 10) {
                        if filteredPages.isEmpty {
                            Text("No matching pages")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 12)
                        } else {
                            ForEach(filteredPages, content: pageButton)
                        }
                    }
                    .padding(8)
                }
            }
        }
        .frame(
            minWidth: compact ? nil : 116,
            idealWidth: compact ? nil : 140,
            maxWidth: compact ? .infinity : 170,
            maxHeight: compact ? 180 : .infinity
        )
        .background(.bar)
    }

    @ViewBuilder
    private func pageButton(_ page: PageSummary) -> some View {
        Button {
            selectedPageID = page.pageID
        } label: {
            VStack(spacing: 6) {
                PageThumbnailView(page: page)
                    .frame(maxWidth: compact ? 84 : .infinity)
                    .aspectRatio(
                        CGFloat(max(page.width, 1)) / CGFloat(max(page.height, 1)),
                        contentMode: .fit
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 5)
                            .stroke(selectedPageID == page.pageID ? Color.accentColor : .clear, lineWidth: 3)
                    }
                Text(pageDisplayName(for: page))
                    .font(.caption)
                    .foregroundStyle(selectedPageID == page.pageID ? .primary : .secondary)
            }
            .padding(4)
        }
        .buttonStyle(.plain)
        #if os(iOS)
        .hoverEffect(.highlight)
        #endif
        .accessibilityLabel(pageDisplayName(for: page))
        .accessibilityAddTraits(selectedPageID == page.pageID ? .isSelected : [])
        .contextMenu {
            Button("Duplicate", systemImage: "plus.square.on.square", action: onDuplicate)
            Button("Move Up", systemImage: "arrow.up", action: { onMove(-1) })
            Button("Move Down", systemImage: "arrow.down", action: { onMove(1) })
            Divider()
            Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
        }
    }

    private var selectedIndex: Int {
        pages.firstIndex(where: { $0.pageID == selectedPageID }) ?? 0
    }

    private var filteredPages: [PageSummary] {
        let query = pageFilter.trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(options: .diacriticInsensitive, locale: .current)
            .lowercased()
        guard !query.isEmpty else { return pages }
        return pages.filter { PageNavigatorFilter.matches($0, query: query) }
    }

    private func pageDisplayName(for page: PageSummary) -> String {
        let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Page \(page.pageIndex + 1)" : title
    }
}

enum PageNavigatorFilter {
    static func matches(_ page: PageSummary, query: String) -> Bool {
        let normalizedQuery = query
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(options: .diacriticInsensitive, locale: .current)
            .lowercased()
        guard !normalizedQuery.isEmpty else { return true }

        let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let displayName = title.isEmpty ? "Page \(page.pageIndex + 1)" : title
        return displayName
            .folding(options: .diacriticInsensitive, locale: .current)
            .lowercased()
            .contains(normalizedQuery)
    }
}

private struct PageSizeEditorView: View {
    @Environment(\.dismiss) private var dismiss
    let initialWidth: Int
    let initialHeight: Int
    let onSave: (Int, Int) -> Void
    @State private var width: String
    @State private var height: String

    init(initialWidth: Int, initialHeight: Int, onSave: @escaping (Int, Int) -> Void) {
        self.initialWidth = initialWidth
        self.initialHeight = initialHeight
        self.onSave = onSave
        _width = State(initialValue: String(initialWidth))
        _height = State(initialValue: String(initialHeight))
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Width", text: $width)
                TextField("Height", text: $height)
                Text("Changing the page size keeps existing stroke coordinates unchanged; content is not rescaled.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .navigationTitle("Custom Page Size")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        guard let parsedWidth = Int(width), let parsedHeight = Int(height) else { return }
                        onSave(parsedWidth, parsedHeight)
                        dismiss()
                    }
                }
            }
        }
    }
}

struct PageThumbnailView: View {
    let page: PageSummary

    var body: some View {
        RoundedRectangle(cornerRadius: 5)
            .fill(.white)
            .overlay {
                #if canImport(PencilKit) && canImport(UIKit)
                if let image = drawingImage {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding(2)
                } else {
                    Image(systemName: "pencil.and.scribble")
                        .foregroundStyle(.secondary)
                }
                #else
                Image(systemName: "pencil.and.scribble")
                    .foregroundStyle(.secondary)
                #endif
            }
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }

    #if canImport(PencilKit) && canImport(UIKit)
    private var drawingImage: UIImage? {
        guard let data = LocalInkCache.readPKDrawing(pageID: page.pageID),
              let drawing = try? PKDrawing(data: data) else { return nil }
        let bounds = CGRect(
            x: 0,
            y: 0,
            width: CGFloat(max(page.width, 1)),
            height: CGFloat(max(page.height, 1))
        )
        return drawing.image(from: bounds, scale: 0.25)
    }
    #endif
}

struct SaveSyncStatusView: View {
    @EnvironmentObject private var model: AppModel
    let document: DocumentRecord
    let pageID: UUID?

    var body: some View {
        HStack(spacing: 12) {
            Label(model.saveState.title, systemImage: saveSymbol)
                .foregroundStyle(saveColor)
                .font(.callout)
            Divider().frame(height: 16)
            Label(syncTitle, systemImage: syncSymbol)
                .foregroundStyle(.secondary)
                .font(.callout)
            if case .failed = model.saveState, let pageID {
                Button("Retry save") {
                    Task { await model.retryLocalSave(pageID: pageID) }
                }
                .font(.callout.weight(.medium))
            }
            Spacer()
            Text("\(document.pages.count) page\(document.pages.count == 1 ? "" : "s")")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(model.saveState.title). \(syncTitle). \(document.pages.count) pages.")
    }

    private var saveSymbol: String {
        switch model.saveState {
        case .saved: return "checkmark.circle.fill"
        case .saving: return "arrow.triangle.2.circlepath"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }

    private var saveColor: Color {
        switch model.saveState {
        case .saved: return .green
        case .saving: return .secondary
        case .failed: return .orange
        }
    }

    private var syncTitle: String {
        switch model.syncState {
        case .idle: return model.isGuest ? "Local only" : "Waiting to sync"
        case .syncing: return "Syncing…"
        case let .online(date): return "Synced \(date.formatted(date: .omitted, time: .shortened))"
        case let .offline(reason): return reason
        case let .failed(reason): return "Needs attention · \(reason)"
        }
    }

    private var syncSymbol: String {
        switch model.syncState {
        case .online: return "checkmark.icloud.fill"
        case .syncing: return "arrow.triangle.2.circlepath.icloud"
        case .failed: return "exclamationmark.icloud.fill"
        case .offline, .idle: return "icloud.slash"
        }
    }
}

struct ImportedSourcePreviewView: View {
    let document: DocumentRecord
    let page: PageSummary

    var body: some View {
        Group {
            #if canImport(PDFKit) && canImport(UIKit)
            if let source = PageSourceMetadata.descriptor(for: document, page: page) {
                if source.kind == "pdf_import" {
                    ImportedPDFPageView(url: source.url, pageIndex: source.sourcePageIndex)
                } else if let image = UIImage(contentsOfFile: source.url.path) {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding()
                } else {
                    unavailable
                }
            } else {
                unavailable
            }
            #else
            unavailable
            #endif
        }
        .navigationTitle("Original source")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }

    private var unavailable: some View {
        ContentUnavailableView(
            "Original unavailable",
            systemImage: "doc.slash",
            description: Text("The durable source file is no longer available on this iPad.")
        )
    }

}

#if canImport(PDFKit) && canImport(UIKit)
private struct ImportedPDFPageView: UIViewRepresentable {
    let url: URL
    let pageIndex: Int

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePage
        view.displayDirection = .horizontal
        view.backgroundColor = .secondarySystemBackground
        return view
    }

    func updateUIView(_ view: PDFView, context: Context) {
        guard let document = PDFDocument(url: url) else {
            view.document = nil
            return
        }
        view.document = document
        if let page = document.page(at: pageIndex) {
            view.go(to: page)
        }
    }
}
#endif

#if os(iOS) && canImport(UIKit)
private struct PrintInteractionView: UIViewControllerRepresentable {
    let pdfURL: URL
    let onCompletion: (Error?) -> Void

    func makeUIViewController(context: Context) -> PrintPresenterViewController {
        PrintPresenterViewController(pdfURL: pdfURL, onCompletion: onCompletion)
    }

    func updateUIViewController(_ viewController: PrintPresenterViewController, context: Context) {}
}

private final class PrintPresenterViewController: UIViewController {
    private let pdfURL: URL
    private let onCompletion: (Error?) -> Void
    private var hasPresented = false

    init(pdfURL: URL, onCompletion: @escaping (Error?) -> Void) {
        self.pdfURL = pdfURL
        self.onCompletion = onCompletion
        super.init(nibName: nil, bundle: nil)
        view.backgroundColor = .clear
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard !hasPresented else { return }
        hasPresented = true

        let controller = UIPrintInteractionController.shared
        let info = UIPrintInfo(dictionary: nil)
        info.jobName = pdfURL.deletingPathExtension().lastPathComponent
        info.outputType = .general
        controller.printInfo = info
        controller.printingItem = pdfURL
        controller.present(animated: true) { [weak self] _, _, error in
            self?.onCompletion(error)
            self?.dismiss(animated: false)
        }
    }
}
#endif
