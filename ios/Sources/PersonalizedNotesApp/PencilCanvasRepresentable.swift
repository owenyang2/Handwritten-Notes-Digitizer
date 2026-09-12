import SwiftUI

struct CanvasUnavailableView: View {
    var body: some View {
        ContentUnavailableView(
            "Apple Pencil canvas unavailable",
            systemImage: "pencil.tip.crop.circle",
            description: Text("PKCanvasView requires an iPad build with PencilKit. This macOS/SPM compile uses a placeholder so kit tests can still run.")
        )
        .padding()
    }
}

#if canImport(PencilKit) && canImport(UIKit)
import PencilKit
import UIKit

/// Hosts `PKCanvasView` (a `UIScrollView` subclass). Pencil draws; fingers pan
/// and zoom. `drawingDidChange` only snapshots bytes and hops off the run
/// loop — it never waits on the network.
struct PencilCanvasRepresentable: UIViewRepresentable {
    var initialDrawingData: Data?
    var canvasSize: CGSize
    var onDrawingCommit: @MainActor (Data) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onDrawingCommit: onDrawingCommit)
    }

    func makeUIView(context: Context) -> PKCanvasView {
        let canvas = PKCanvasView()
        canvas.delegate = context.coordinator
        canvas.drawingPolicy = .pencilOnly
        canvas.alwaysBounceVertical = true
        canvas.alwaysBounceHorizontal = true
        canvas.minimumZoomScale = 0.5
        canvas.maximumZoomScale = 4
        canvas.bouncesZoom = true
        canvas.backgroundColor = .white
        canvas.isOpaque = true
        canvas.contentSize = canvasSize
        canvas.tool = PKInkingTool(.pen, color: .black, width: 4)
        if let initialDrawingData, let drawing = try? PKDrawing(data: initialDrawingData) {
            context.coordinator.isApplyingExternalDrawing = true
            canvas.drawing = drawing
            context.coordinator.isApplyingExternalDrawing = false
        }
        return canvas
    }

    func updateUIView(_ canvas: PKCanvasView, context: Context) {
        context.coordinator.onDrawingCommit = onDrawingCommit
        attachToolPicker(to: canvas, coordinator: context.coordinator)
        if canvas.contentSize != canvasSize {
            canvas.contentSize = canvasSize
        }
    }

    static func dismantleUIView(_ canvas: PKCanvasView, coordinator: Coordinator) {
        coordinator.toolPicker?.setVisible(false, forFirstResponder: canvas)
        coordinator.toolPicker?.removeObserver(canvas)
        coordinator.toolPicker = nil
    }

    private func attachToolPicker(to canvas: PKCanvasView, coordinator: Coordinator) {
        let picker = coordinator.toolPicker ?? PKToolPicker()
        picker.addObserver(canvas)
        picker.setVisible(true, forFirstResponder: canvas)
        canvas.becomeFirstResponder()
        coordinator.toolPicker = picker
    }

    final class Coordinator: NSObject, PKCanvasViewDelegate {
        var onDrawingCommit: @MainActor (Data) -> Void
        var toolPicker: PKToolPicker?
        var isApplyingExternalDrawing = false
        private var commitTask: Task<Void, Never>?

        init(onDrawingCommit: @escaping @MainActor (Data) -> Void) {
            self.onDrawingCommit = onDrawingCommit
        }

        func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
            guard !isApplyingExternalDrawing else { return }
            let data = canvasView.drawing.dataRepresentation()
            commitTask?.cancel()
            let commit = onDrawingCommit
            // Snapshot now, persist later. Do not call the backend here.
            commitTask = Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(50))
                guard !Task.isCancelled else { return }
                commit(data)
            }
        }
    }
}
#else
struct PencilCanvasRepresentable: View {
    var initialDrawingData: Data?
    var canvasSize: CGSize
    var onDrawingCommit: @MainActor (Data) -> Void

    var body: some View {
        CanvasUnavailableView()
    }
}
#endif
