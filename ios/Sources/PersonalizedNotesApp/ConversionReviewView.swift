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
    @State private var exportedFile: ShareableExportFile?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack {
                    Button("Done") { dismiss() }
                    Spacer()
                    #if os(iOS)
                    EditButton()
                    #endif
                    Button("Save All") { perform { try await saveAll(); try await reload(); message = "Review saved." } }
                        .disabled(busy || blocks.isEmpty || blocks.contains { $0.sourceStale == true })
                    Button("Export") { choosingExportFormat = true }
                    .confirmationDialog("Export saved review", isPresented: $choosingExportFormat, titleVisibility: .visible) {
                        ForEach(["markdown", "latex", "html"], id: \.self) { format in
                            Button(format.capitalized) { perform { try await export(format) } }
                        }
                        Button("Cancel", role: .cancel) { }
                    }
                    .accessibilityIdentifier("conversion-export")
                    .disabled(busy || blocks.isEmpty || blocks.contains { $0.sourceStale == true })
                }
                .buttonStyle(.borderless)
                .padding()
                if let message { Text(message).font(.callout).padding().accessibilityIdentifier("conversion-review-status") }
                #if canImport(UIKit)
                if let source, let image = UIImage(data: source) {
                    GeometryReader { geometry in
                        let scale = min(geometry.size.width / image.size.width, geometry.size.height / image.size.height)
                        ZStack(alignment: .topLeading) {
                            Image(uiImage: image).resizable().frame(width: image.size.width*scale, height: image.size.height*scale)
                            if let block = blocks.first(where: { $0.regionID == selected }), block.bbox.count == 4 {
                                Rectangle().stroke(Color.accentColor, lineWidth: 3)
                                    .background(Color.accentColor.opacity(0.12))
                                    .frame(width: CGFloat(block.bbox[2])*scale, height: CGFloat(block.bbox[3])*scale)
                                    .offset(x: CGFloat(block.bbox[0])*scale, y: CGFloat(block.bbox[1])*scale)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .frame(height: 240)
                    .padding()
                    .accessibilityLabel("Saved source revision with selected region highlighted")
                }
                #endif
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
                                Button("Accept / Save") { perform { try await save(block); try await reload(); message = "Review saved." } }
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
                if blocks.isEmpty && !busy { Text("No review blocks. Select a region on the website or retry conversion.").padding() }
            }
            .navigationTitle("Review Conversion")
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
    }

    private func saveAll() async throws {
        for block in blocks { try await save(block) }
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

    private func export(_ format: String) async throws {
        try await saveAll()
        let exported = try await model.client.exportConvertedDocument(documentID: run.documentID, format: format, revisionID: revisionID)
        let suffix = format == "latex" ? "tex" : format == "markdown" ? "md" : "html"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("Converted-Notes-\(UUID().uuidString).\(suffix)")
        try Data(exported.text.utf8).write(to: url, options: .atomic)
        exportedFile = ShareableExportFile(url: url)
        message = "Exported the saved review revision."
        try await reload()
    }
}
