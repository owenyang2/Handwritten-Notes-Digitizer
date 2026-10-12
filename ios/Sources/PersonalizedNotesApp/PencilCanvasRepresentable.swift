import SwiftUI
import Foundation
import PersonalizedNotesKit

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
#if canImport(PDFKit)
import PDFKit
#endif

/// Hosts `PKCanvasView` (a `UIScrollView` subclass). Pencil draws; fingers pan
/// and zoom. `drawingDidChange` only snapshots bytes and hops off the run
/// loop — it never waits on the network.
struct PencilCanvasRepresentable: UIViewRepresentable {
    var pageID: UUID
    var initialDrawingData: Data?
    var drawingRevisionID: UUID?
    var canvasSize: CGSize
    var allowsFingerDrawing: Bool
    var backgroundSourceURL: URL?
    var backgroundSourceKind: String?
    var backgroundPageIndex: Int
    var paperTemplate: String?
    var command: PencilCanvasCommand?
    var onDrawingCommit: @MainActor (UUID, Data) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(pageID: pageID, onDrawingCommit: onDrawingCommit)
    }

    func makeUIView(context: Context) -> PKCanvasView {
        let canvas = PKCanvasView()
        canvas.delegate = context.coordinator
        context.coordinator.canvas = canvas
        canvas.drawingPolicy = allowsFingerDrawing ? .anyInput : .pencilOnly
        canvas.alwaysBounceVertical = true
        canvas.alwaysBounceHorizontal = true
        canvas.minimumZoomScale = 0.5
        canvas.maximumZoomScale = 4
        canvas.bouncesZoom = true
        canvas.backgroundColor = .clear
        canvas.isOpaque = false
        canvas.contentSize = canvasSize
        canvas.tool = PKInkingTool(.pen, color: .black, width: 4)
        context.coordinator.installBackground(
            on: canvas,
            sourceURL: backgroundSourceURL,
            sourceKind: backgroundSourceKind,
            pageIndex: backgroundPageIndex,
            paperTemplate: paperTemplate,
            size: canvasSize
        )
        context.coordinator.updateDrawing(
            on: canvas, pageID: pageID, data: initialDrawingData, revisionID: drawingRevisionID
        )
        if #available(iOS 17.5, *) {
            let pencilInteraction = UIPencilInteraction(delegate: context.coordinator)
            canvas.addInteraction(pencilInteraction)
            context.coordinator.pencilInteraction = pencilInteraction
        }
        return canvas
    }

    func updateUIView(_ canvas: PKCanvasView, context: Context) {
        context.coordinator.onDrawingCommit = onDrawingCommit
        if let command, context.coordinator.lastCommandID != command.id {
            context.coordinator.lastCommandID = command.id
            context.coordinator.pendingCommand = command
        }
        context.coordinator.updateDrawing(
            on: canvas, pageID: pageID, data: initialDrawingData, revisionID: drawingRevisionID
        )
        canvas.drawingPolicy = allowsFingerDrawing ? .anyInput : .pencilOnly
        context.coordinator.updateBackground(
            on: canvas,
            sourceURL: backgroundSourceURL,
            sourceKind: backgroundSourceKind,
            pageIndex: backgroundPageIndex,
            paperTemplate: paperTemplate,
            size: canvasSize
        )
        attachToolPicker(to: canvas, coordinator: context.coordinator)
        if canvas.contentSize != canvasSize {
            canvas.contentSize = canvasSize
        }
    }

    static func dismantleUIView(_ canvas: PKCanvasView, coordinator: Coordinator) {
        coordinator.flush(canvas)
        if #available(iOS 17.5, *), let pencilInteraction = coordinator.pencilInteraction {
            canvas.removeInteraction(pencilInteraction)
        }
        coordinator.pencilInteraction = nil
        coordinator.canvas = nil
        coordinator.toolPicker?.setVisible(false, forFirstResponder: canvas)
        coordinator.toolPicker?.removeObserver(canvas)
        coordinator.toolPicker = nil
    }

    private func attachToolPicker(to canvas: PKCanvasView, coordinator: Coordinator) {
        if coordinator.toolPicker == nil {
            let picker = PKToolPicker()
            picker.addObserver(canvas)
            picker.setVisible(true, forFirstResponder: canvas)
            canvas.becomeFirstResponder()
            coordinator.toolPicker = picker
        }
        if let command = coordinator.pendingCommand {
            coordinator.apply(command, to: canvas)
            coordinator.pendingCommand = nil
        }
    }

    final class Coordinator: NSObject, PKCanvasViewDelegate, UIPencilInteractionDelegate {
        var pageID: UUID
        var onDrawingCommit: @MainActor (UUID, Data) -> Void
        var toolPicker: PKToolPicker?
        weak var canvas: PKCanvasView?
        var pencilInteraction: UIPencilInteraction?
        var isApplyingExternalDrawing = false
        private var commitTask: Task<Void, Never>?
        private var drawingRevisionID: UUID?
        private var appliedDrawing: PKDrawing?
        private var pendingDrawingData: Data?
        private var submittedDrawing: PKDrawing?
        var lastCommandID: UUID?
        private var previousTool: PKTool?

        var pendingCommand: PencilCanvasCommand?
        private var backgroundKey: String?
        private var backgroundImageView: UIImageView?

        init(pageID: UUID, onDrawingCommit: @escaping @MainActor (UUID, Data) -> Void) {
            self.pageID = pageID
            self.onDrawingCommit = onDrawingCommit
        }

        func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
            guard !isApplyingExternalDrawing else { return }
            let data = canvasView.drawing.dataRepresentation()
            guard data != pendingDrawingData else { return }
            if pendingDrawingData == nil, canvasView.drawing == (submittedDrawing ?? appliedDrawing) { return }
            pendingDrawingData = data
            commitTask?.cancel()
            // Snapshot now, persist later. Do not call the backend here.
            commitTask = Task { @MainActor [weak self, weak canvasView] in
                try? await Task.sleep(for: .milliseconds(50))
                guard !Task.isCancelled, let self, let canvasView else { return }
                self.flush(canvasView)
            }
        }

        func updateDrawing(on canvas: PKCanvasView, pageID: UUID, data: Data?, revisionID: UUID?) {
            if self.pageID != pageID {
                flush(canvas)
                self.pageID = pageID
                appliedDrawing = nil
                submittedDrawing = nil
                drawingRevisionID = nil
                canvas.setZoomScale(1, animated: false)
                canvas.setContentOffset(.zero, animated: false)
            }
            guard appliedDrawing == nil || drawingRevisionID != revisionID else { return }
            let drawing = data.flatMap { try? PKDrawing(data: $0) } ?? PKDrawing()
            if drawing == canvas.drawing {
                appliedDrawing = canvas.drawing
                drawingRevisionID = revisionID
                submittedDrawing = nil
                return
            }
            // A submitted save can still be committing on the background queue.
            // Neither it nor an unsubmitted Pencil snapshot may be replaced.
            guard pendingDrawingData == nil, submittedDrawing == nil else { return }
            isApplyingExternalDrawing = true
            canvas.drawing = drawing
            isApplyingExternalDrawing = false
            appliedDrawing = canvas.drawing
            drawingRevisionID = revisionID
        }

        func installBackground(
            on canvas: PKCanvasView,
            sourceURL: URL?,
            sourceKind: String?,
            pageIndex: Int,
            paperTemplate: String?,
            size: CGSize
        ) {
            let imageView = UIImageView(frame: CGRect(origin: .zero, size: size))
            imageView.contentMode = .scaleToFill
            imageView.isUserInteractionEnabled = false
            imageView.image = sourceImage(
                sourceURL: sourceURL,
                sourceKind: sourceKind,
                pageIndex: pageIndex,
                paperTemplate: paperTemplate,
                size: size
            )
            canvas.insertSubview(imageView, at: 0)
            backgroundImageView = imageView
            backgroundKey = backgroundKeyValue(
                sourceURL: sourceURL,
                sourceKind: sourceKind,
                pageIndex: pageIndex,
                paperTemplate: paperTemplate,
                size: size
            )
        }

        func updateBackground(
            on canvas: PKCanvasView,
            sourceURL: URL?,
            sourceKind: String?,
            pageIndex: Int,
            paperTemplate: String?,
            size: CGSize
        ) {
            let key = backgroundKeyValue(
                sourceURL: sourceURL,
                sourceKind: sourceKind,
                pageIndex: pageIndex,
                paperTemplate: paperTemplate,
                size: size
            )
            backgroundImageView?.frame = CGRect(origin: .zero, size: size)
            guard key != backgroundKey else { return }
            backgroundImageView?.image = sourceImage(
                sourceURL: sourceURL,
                sourceKind: sourceKind,
                pageIndex: pageIndex,
                paperTemplate: paperTemplate,
                size: size
            )
            backgroundKey = key
            if let backgroundImageView {
                canvas.sendSubviewToBack(backgroundImageView)
            }
        }

        private func backgroundKeyValue(
            sourceURL: URL?,
            sourceKind: String?,
            pageIndex: Int,
            paperTemplate: String?,
            size: CGSize
        ) -> String {
            [
                sourceURL?.path ?? "none",
                sourceKind ?? "none",
                String(pageIndex),
                paperTemplate ?? "blank",
                String(Int(size.width.rounded())),
                String(Int(size.height.rounded())),
            ].joined(separator: "|")
        }

        private func sourceImage(
            sourceURL: URL?,
            sourceKind: String?,
            pageIndex: Int,
            paperTemplate: String?,
            size: CGSize
        ) -> UIImage? {
            if let sourceURL, let sourceKind {
                if sourceKind == "pdf_import" {
                    #if canImport(PDFKit)
                    guard let document = PDFDocument(url: sourceURL),
                          let page = document.page(at: pageIndex) else { return nil }
                    return page.thumbnail(of: size, for: .mediaBox)
                    #else
                    return nil
                    #endif
                }
                return UIImage(contentsOfFile: sourceURL.path)
            }
            return PagePaperTemplateRenderer.image(template: paperTemplate, size: size)
        }

        func flush(_ canvas: PKCanvasView) {
            commitTask?.cancel()
            commitTask = nil
            guard let data = pendingDrawingData else { return }
            pendingDrawingData = nil
            submittedDrawing = canvas.drawing
            onDrawingCommit(pageID, data)
        }

        func apply(_ command: PencilCanvasCommand, to canvas: PKCanvasView) {
            switch command.action {
            case .undo:
                canvas.undoManager?.undo()
            case .redo:
                canvas.undoManager?.redo()
            case .resetZoom:
                canvas.setZoomScale(1, animated: true)
                canvas.setContentOffset(.zero, animated: true)
            case .save:
                flush(canvas)
            case .cutSelection:
                canvas.cut(nil)
            case .copySelection:
                canvas.copy(nil)
            case .pasteSelection:
                canvas.paste(nil)
            case .toggleRuler:
                canvas.isRulerActive.toggle()
            case let .selectTool(tool):
                rememberCurrentTool(canvas)
                canvas.tool = tool.pkTool(color: .black, width: nil)
            case let .selectPreset(preset):
                rememberCurrentTool(canvas)
                canvas.tool = preset.pkTool
            }
        }

        @available(iOS 17.5, *)
        func pencilInteraction(
            _ interaction: UIPencilInteraction,
            didReceiveTap tap: UIPencilInteraction.Tap
        ) {
            handlePencilAction(UIPencilInteraction.preferredTapAction)
        }

        @available(iOS 17.5, *)
        func pencilInteraction(
            _ interaction: UIPencilInteraction,
            didReceiveSqueeze squeeze: UIPencilInteraction.Squeeze
        ) {
            guard squeeze.phase == .ended else { return }
            handlePencilAction(UIPencilInteraction.preferredSqueezeAction)
        }

        @available(iOS 17.5, *)
        private func handlePencilAction(_ action: UIPencilPreferredAction) {
            guard let canvas else { return }
            switch action {
            case .switchEraser:
                if canvas.tool is PKEraserTool {
                    canvas.tool = previousTool ?? PKInkingTool(.pen, color: .black, width: 4)
                } else {
                    rememberCurrentTool(canvas)
                    canvas.tool = PKEraserTool(.vector)
                }
            case .switchPrevious:
                guard let previousTool else { return }
                let currentTool = canvas.tool
                canvas.tool = previousTool
                self.previousTool = currentTool
            case .showColorPalette, .showInkAttributes, .showContextualPalette:
                // The native picker remains the source of truth for these
                // palettes. Reassert first responder/visibility so the
                // system action is still useful when the picker was moved or
                // temporarily hidden by a compact layout.
                canvas.becomeFirstResponder()
                toolPicker?.setVisible(true, forFirstResponder: canvas)
            case .ignore, .runSystemShortcut:
                break
            @unknown default:
                break
            }
        }

        private func rememberCurrentTool(_ canvas: PKCanvasView) {
            guard !(canvas.tool is PKEraserTool), !(canvas.tool is PKLassoTool) else { return }
            previousTool = canvas.tool
        }
    }
}
#else
struct PencilCanvasRepresentable: View {
    var pageID: UUID
    var initialDrawingData: Data?
    var canvasSize: CGSize
    var allowsFingerDrawing: Bool
    var backgroundSourceURL: URL?
    var backgroundSourceKind: String?
    var backgroundPageIndex: Int
    var paperTemplate: String?
    var command: PencilCanvasCommand?
    var onDrawingCommit: @MainActor (UUID, Data) -> Void

    var body: some View {
        CanvasUnavailableView()
    }
}
#endif

struct PencilCanvasCommand: Equatable {
    enum Tool: String, Codable, Equatable, Hashable {
        case pen
        case pencil
        case marker
        case eraser
        case lasso
    }

    enum Action: Equatable {
        case undo
        case redo
        case resetZoom
        case save
        case cutSelection
        case copySelection
        case pasteSelection
        case toggleRuler
        case selectTool(Tool)
        case selectPreset(CanvasToolPreset)
    }

    let id = UUID()
    let action: Action

    init(action: Action) {
        self.action = action
    }
}

struct CanvasToolPreset: Codable, Equatable, Hashable, Identifiable {
    let id: String
    let name: String
    let tool: PencilCanvasCommand.Tool
    let colorHex: String
    let width: Double
    let opacity: Double

    var systemImage: String {
        switch tool {
        case .pen: return "pencil.line"
        case .pencil: return "pencil"
        case .marker: return "highlighter"
        case .eraser: return "eraser"
        case .lasso: return "lasso"
        }
    }
}

enum CanvasToolPresetStore {
    private static let defaultsKey = "personalized-notes.canvas-tool-presets"

    static let builtIn: [CanvasToolPreset] = [
        CanvasToolPreset(
            id: "black-pen",
            name: "Black Pen",
            tool: .pen,
            colorHex: "#111111",
            width: 4,
            opacity: 1
        ),
        CanvasToolPreset(
            id: "blue-pen",
            name: "Blue Pen",
            tool: .pen,
            colorHex: "#1D4ED8",
            width: 3,
            opacity: 1
        ),
        CanvasToolPreset(
            id: "red-pen",
            name: "Red Pen",
            tool: .pen,
            colorHex: "#DC2626",
            width: 3,
            opacity: 1
        ),
        CanvasToolPreset(
            id: "yellow-highlighter",
            name: "Yellow Highlighter",
            tool: .marker,
            colorHex: "#FACC15",
            width: 18,
            opacity: 0.45
        ),
        CanvasToolPreset(
            id: "vector-eraser",
            name: "Vector Eraser",
            tool: .eraser,
            colorHex: "#000000",
            width: 0,
            opacity: 1
        ),
    ]

    static func load() -> [CanvasToolPreset] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let presets = try? APIJSON.decoder().decode([CanvasToolPreset].self, from: data),
              !presets.isEmpty else {
            save(builtIn)
            return builtIn
        }
        return presets
    }

    static func save(_ presets: [CanvasToolPreset]) {
        guard let data = try? APIJSON.encoder().encode(Array(presets.prefix(5))) else { return }
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }
}

#if canImport(PencilKit) && canImport(UIKit)
private extension PencilCanvasCommand.Tool {
    func pkTool(color: UIColor, width: CGFloat?) -> PKTool {
        switch self {
        case .pen:
            return PKInkingTool(.pen, color: color, width: width ?? 4)
        case .pencil:
            return PKInkingTool(.pencil, color: color, width: width ?? 4)
        case .marker:
            return PKInkingTool(.marker, color: color, width: width ?? 10)
        case .eraser:
            return PKEraserTool(.vector)
        case .lasso:
            return PKLassoTool()
        }
    }
}

private extension CanvasToolPreset {
    var pkTool: PKTool {
        let color = UIColor(hex: colorHex).withAlphaComponent(CGFloat(min(max(opacity, 0), 1)))
        return tool.pkTool(color: color, width: width > 0 ? CGFloat(width) : nil)
    }
}

private extension UIColor {
    convenience init(hex: String) {
        let normalized = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var value: UInt64 = 0
        Scanner(string: normalized).scanHexInt64(&value)
        let red = CGFloat((value >> 16) & 0xFF) / 255
        let green = CGFloat((value >> 8) & 0xFF) / 255
        let blue = CGFloat(value & 0xFF) / 255
        self.init(red: red, green: green, blue: blue, alpha: 1)
    }
}
#endif
