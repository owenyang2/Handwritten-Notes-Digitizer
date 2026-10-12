import Foundation

enum PagePaperTemplate: String, CaseIterable, Codable, Sendable {
    case blank
    case ruled
    case grid
    case dotGrid = "dot_grid"
    case cornell

    var title: String {
        switch self {
        case .blank: return "Blank"
        case .ruled: return "Ruled"
        case .grid: return "Grid"
        case .dotGrid: return "Dot Grid"
        case .cornell: return "Cornell"
        }
    }
}

#if canImport(UIKit)
import UIKit

enum PagePaperTemplateRenderer {
    static func image(template rawValue: String?, size: CGSize) -> UIImage {
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { context in
            draw(template: rawValue, in: CGRect(origin: .zero, size: size), context: context.cgContext)
        }
    }

    static func draw(template rawValue: String?, in rect: CGRect, context: CGContext) {
        let template = PagePaperTemplate(rawValue: rawValue ?? PagePaperTemplate.blank.rawValue)
            ?? .blank
        context.saveGState()
        UIColor.white.setFill()
        context.fill(rect)

        guard template != .blank else {
            context.restoreGState()
            return
        }

        context.setLineWidth(1)
        context.setStrokeColor(UIColor(red: 0.72, green: 0.82, blue: 0.96, alpha: 0.75).cgColor)
        switch template {
        case .ruled:
            drawHorizontalLines(in: rect, step: 32, context: context)
        case .grid:
            drawGrid(in: rect, step: 32, context: context)
        case .dotGrid:
            context.setFillColor(UIColor(red: 0.62, green: 0.68, blue: 0.76, alpha: 0.7).cgColor)
            var y = rect.minY + 24
            while y < rect.maxY {
                var x = rect.minX + 24
                while x < rect.maxX {
                    context.fillEllipse(in: CGRect(x: x - 1, y: y - 1, width: 2, height: 2))
                    x += 24
                }
                y += 24
            }
        case .cornell:
            drawHorizontalLines(in: rect, step: 32, context: context)
            context.setStrokeColor(UIColor(red: 0.85, green: 0.32, blue: 0.32, alpha: 0.65).cgColor)
            context.move(to: CGPoint(x: rect.minX + rect.width * 0.28, y: rect.minY))
            context.addLine(to: CGPoint(x: rect.minX + rect.width * 0.28, y: rect.maxY))
            context.move(to: CGPoint(x: rect.minX, y: rect.minY + rect.height * 0.8))
            context.addLine(to: CGPoint(x: rect.maxX, y: rect.minY + rect.height * 0.8))
            context.strokePath()
        case .blank:
            break
        }
        context.restoreGState()
    }

    private static func drawHorizontalLines(in rect: CGRect, step: CGFloat, context: CGContext) {
        var y = rect.minY + step
        while y < rect.maxY {
            context.move(to: CGPoint(x: rect.minX, y: y))
            context.addLine(to: CGPoint(x: rect.maxX, y: y))
            y += step
        }
        context.strokePath()
    }

    private static func drawGrid(in rect: CGRect, step: CGFloat, context: CGContext) {
        var x = rect.minX + step
        while x < rect.maxX {
            context.move(to: CGPoint(x: x, y: rect.minY))
            context.addLine(to: CGPoint(x: x, y: rect.maxY))
            x += step
        }
        drawHorizontalLines(in: rect, step: step, context: context)
    }
}
#endif
