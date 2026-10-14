import PersonalizedNotesKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

@MainActor
struct ConversionReviewView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let run: RecognitionRun
    @State private var blocks: [ConversionBlock] = []
    @State private var drafts: [UUID: String] = [:]
    @State private var exclusions: [UUID: Bool] = [:]
    @State private var selected: UUID?
    @State private var source: Data?
    @State private var revisionID: UUID?
    @State private var message: String?
    @State private var busy = false
    @State private var choosingExportFormat = false
    @State private var confirmingClose = false
    @State private var savedTexts: [UUID: String] = [:]
    @State private var savedExclusions: [UUID: Bool] = [:]
    @State private var exportedFile: ShareableExportFile?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack {
                    Button("Done") { if hasUnsavedChanges { confirmingClose = true } else { dismiss() } }
                    Spacer()
                    #if os(iOS)
                    EditButton()
                    #endif
                    Button("Save review") { perform { try await saveAll(); try await reload(); message = "Review saved." } }
                        .disabled(busy || blocks.isEmpty || blocks.contains { $0.sourceStale == true })
                    Button("Export") { choosingExportFormat = true }
                    .confirmationDialog(hasUnsavedChanges ? "Save changes and export" : "Export saved review", isPresented: $choosingExportFormat, titleVisibility: .visible) {
                        ForEach(["markdown", "latex", "html"], id: \.self) { format in
                            Button("Save and export \(format.capitalized)") { perform { try await export(format, saving: true) } }
                            if hasUnsavedChanges {
                                Button("Export last saved \(format.capitalized)") { perform { try await export(format, saving: false) } }
                            }
                        }
                        Button("Cancel", role: .cancel) { }
                    }
                    .accessibilityIdentifier("conversion-export")
                    .disabled(busy || blocks.isEmpty || blocks.contains { $0.sourceStale == true })
                }
                .buttonStyle(.borderless)
                .padding()
                if let message { Text(message).font(.callout).padding().accessibilityIdentifier("conversion-review-status") }
                GeometryReader { geometry in
                    let wide = geometry.size.width >= 760
                    let layout = wide ? AnyLayout(HStackLayout(spacing: 0)) : AnyLayout(VStackLayout(spacing: 0))
                    layout {
                        sourcePane
                            .frame(maxWidth: .infinity)
                            .frame(height: wide ? geometry.size.height : min(220, geometry.size.height * 0.3))
                        resultPane
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
                if blocks.isEmpty && !busy { Text("No review blocks. Select a region on the website or retry conversion.").padding() }
            }
            .navigationTitle("Review")
            .interactiveDismissDisabled(hasUnsavedChanges || busy)
            .confirmationDialog("Changes not saved", isPresented: $confirmingClose, titleVisibility: .visible) {
                Button("Save review and close") { perform { try await saveAll(); dismiss() } }
                Button("Discard changes", role: .destructive) { dismiss() }
                Button("Keep editing", role: .cancel) { }
            } message: {
                Text("Your corrections have not been saved to this notebook.")
            }
            .task { perform { source = try await model.client.conversionSource(runID: run.runID); try await reload() } }
            .sheet(item: $exportedFile) { file in
                #if canImport(UIKit)
                ActivityShareView(file: file)
                #else
                Text(file.url.path)
                #endif
            }
        }
    }

    private var hasUnsavedChanges: Bool {
        blocks.contains { block in
            (drafts[block.id] ?? "") != (savedTexts[block.id] ?? block.reviewedText ?? block.predictionText ?? "") ||
            (exclusions[block.id] ?? false) != (savedExclusions[block.id] ?? block.excluded)
        }
    }

    @ViewBuilder private var sourcePane: some View {
        #if canImport(UIKit)
                if let source, let image = UIImage(data: source) {
                    GeometryReader { geometry in
                        let pixelWidth = CGFloat(image.cgImage?.width ?? Int(image.size.width * image.scale))
                        let pixelHeight = CGFloat(image.cgImage?.height ?? Int(image.size.height * image.scale))
                        let scale = min(geometry.size.width / pixelWidth, geometry.size.height / pixelHeight)
                        ZStack(alignment: .topLeading) {
                            Image(uiImage: image).resizable().frame(width: pixelWidth*scale, height: pixelHeight*scale)
                            ForEach(blocks.filter { $0.bbox.count == 4 }) { block in
                                Button { selected = block.regionID } label: {
                                    Rectangle().stroke(Color.blue, lineWidth: selected == block.regionID ? 3 : 1)
                                        .background(Color.blue.opacity(selected == block.regionID ? 0.12 : 0.01))
                                }
                                    .buttonStyle(.plain)
                                    .accessibilityLabel("Select original region \(block.orderIndex + 1)")
                                    .frame(width: CGFloat(block.bbox[2])*scale, height: CGFloat(block.bbox[3])*scale)
                                    .offset(x: CGFloat(block.bbox[0])*scale, y: CGFloat(block.bbox[1])*scale)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .padding()
                    .accessibilityLabel("Saved source revision with selected region highlighted")
                }
        #else
        Text("Open the source page beside this review.").foregroundStyle(.secondary)
        #endif
    }

    private var resultPane: some View {
        VStack(spacing: 0) {
            Text(hasUnsavedChanges ? "Changes not saved" : blocks.contains { $0.reviewedText == nil && savedTexts[$0.id] == nil } ? "Prediction · Save review to accept" : "Review matches saved content")
                .font(.callout).foregroundStyle(.secondary).padding(.horizontal)
                List {
                    ForEach(blocks) { block in
                        VStack(alignment: .leading, spacing: 8) {
                            Button {
                                selected = block.regionID
                            } label: {
                                Label(block.contentType.contains("math") ? "Formula" : "Prose", systemImage: selected == block.regionID ? "scope" : "text.viewfinder")
                            }.buttonStyle(.borderless)
                            if block.truncated { Text("Incomplete recognition — review the source or retry.").foregroundStyle(.orange) }
                            if block.sourceStale == true { Text("Source changed. Convert the current page again.").foregroundStyle(.orange) }
                            TextEditor(text: Binding(get: { drafts[block.regionID] ?? "" }, set: { drafts[block.regionID] = $0 }))
                                .frame(minHeight: 78)
                                .accessibilityIdentifier("conversion-text-\(block.regionID.uuidString)")
                            Toggle("Exclude from export", isOn: Binding(get: { exclusions[block.regionID] ?? false }, set: { exclusions[block.regionID] = $0 }))
                            HStack {
                                Button("Save review") { perform { try await save(block); try await reload(); message = "Review saved." } }
                                    .accessibilityIdentifier("conversion-save-\(block.regionID.uuidString)")
                                    .disabled(block.sourceStale == true)
                                Spacer()
                                Menu("Region actions") {
                                    Button("Retry recognition") { perform { try await saveAll(); try await retry([block.regionID]) } }
                                    Button("Split into two lines") {
                                        perform {
                                            try await saveAll()
                                            guard block.bbox.count == 4, block.bbox[3] >= 4 else { return }
                                            let b = block.bbox, half = b[3]/2
                                            try await model.client.splitConversionRegion(regionID: block.regionID,
                                                boxes: [[b[0], b[1], b[2], half], [b[0], b[1]+half, b[2], b[3]-half]])
                                            blocks = try await model.client.pageRegions(pageID: run.pageID)
                                            try await retry(blocks.map(\.regionID))
                                        }
                                    }
                                    if let index = blocks.firstIndex(where: { $0.id == block.id }), index+1 < blocks.count {
                                        Button("Merge with next region") {
                                            perform {
                                                try await saveAll()
                                                try await model.client.mergeConversionRegions(pageID: run.pageID, regionIDs: [block.id, blocks[index+1].id])
                                                blocks = try await model.client.pageRegions(pageID: run.pageID)
                                                try await retry(blocks.map(\.regionID))
                                            }
                                        }
                                    }
                                }
                            }.buttonStyle(.borderless)
                        }
                        .padding(.vertical, 6)
                        .listRowBackground(selected == block.regionID ? Color.accentColor.opacity(0.08) : Color.clear)
                    }
                    .onMove { from, to in
                        blocks.move(fromOffsets: from, toOffset: to)
                        perform { try await model.client.reorderConversionRegions(pageID: run.pageID, regionIDs: blocks.map(\.id)) }
                    }
                }
                .disabled(busy)
        }
    }

    private func perform(_ operation: @escaping @MainActor () async throws -> Void) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do { try await operation() }
            catch { message = error.localizedDescription }
        }
    }

    private func reload() async throws {
        let current = model.conversionRuns[run.pageID] ?? run
        blocks = try await model.client.conversionBlocks(runID: current.runID)
        source = try await model.client.conversionSource(runID: current.runID)
        for block in blocks {
            if drafts[block.id] == nil { drafts[block.id] = block.reviewedText ?? block.predictionText ?? "" }
            if exclusions[block.id] == nil { exclusions[block.id] = block.excluded }
        }
        if selected == nil || !blocks.contains(where: { $0.id == selected }) { selected = blocks.first?.id }
    }

    private func save(_ block: ConversionBlock) async throws {
        let saved = try await model.client.saveConversionReview(block: block, text: drafts[block.id] ?? "", excluded: exclusions[block.id] ?? false)
        revisionID = saved.documentRevisionID
        savedTexts[block.id] = drafts[block.id] ?? ""
        savedExclusions[block.id] = exclusions[block.id] ?? false
    }

    private func saveAll() async throws {
        for block in blocks {
            let text = drafts[block.id] ?? ""
            let excluded = exclusions[block.id] ?? false
            if text != (savedTexts[block.id] ?? block.reviewedText) || excluded != (savedExclusions[block.id] ?? block.excluded) {
                try await save(block)
            }
        }
    }

    private func retry(_ regionIDs: [UUID]) async throws {
        var current = try await model.client.createRecognitionRun(pageID: run.pageID,
            request: RecognitionRunRequest(mode: run.mode, regionIDs: regionIDs))
        for _ in 0..<120 {
            if current.isTerminal { break }
            try await Task.sleep(for: .milliseconds(500))
            current = try await model.client.getRecognitionRun(runID: current.runID)
        }
        model.retainConversionRun(current)
        if !current.isSuccessful { message = current.errorMessage ?? "Recognition is still running." }
        try await reload()
    }

    private func export(_ format: String, saving: Bool) async throws {
        if saving { try await saveAll() }
        let exported = try await model.client.exportConvertedDocument(documentID: run.documentID, format: format, revisionID: revisionID)
        let suffix = format == "latex" ? "tex" : format == "markdown" ? "md" : "html"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("Converted-Notes-\(UUID().uuidString).\(suffix)")
        try Data(exported.text.utf8).write(to: url, options: .atomic)
        exportedFile = ShareableExportFile(url: url)
        message = "Exported the saved review revision."
        try await reload()
    }
}
