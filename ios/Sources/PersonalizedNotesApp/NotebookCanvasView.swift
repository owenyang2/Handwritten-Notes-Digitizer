import PersonalizedNotesKit
import SwiftUI

struct NotebookCanvasView: View {
    @EnvironmentObject private var model: AppModel
    let document: DocumentRecord

    private var page: PageSummary {
        document.pages.sorted { $0.pageIndex < $1.pageIndex }.first
            ?? PageSummary(
                pageID: document.documentID,
                pageIndex: 0,
                width: 768,
                height: 1024,
                dpi: 132,
                coordinateSpace: "points"
            )
    }

    var body: some View {
        Group {
            #if canImport(PencilKit) && canImport(UIKit)
            PencilCanvasRepresentable(
                initialDrawingData: LocalInkCache.readPKDrawing(pageID: page.pageID),
                canvasSize: CGSize(width: CGFloat(max(page.width, 1)), height: CGFloat(max(page.height, 1))),
                onDrawingCommit: { data in
                    model.commitDrawingLocally(
                        pageID: page.pageID,
                        pkDrawingData: data,
                        width: Double(page.width),
                        height: Double(page.height)
                    )
                }
            )
            .ignoresSafeArea(edges: .bottom)
            #else
            CanvasUnavailableView()
            #endif
        }
        .navigationTitle(document.title)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Library") { model.closeNotebook() }
            }
        }
    }
}
