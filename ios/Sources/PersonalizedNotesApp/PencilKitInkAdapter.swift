#if canImport(PencilKit) && canImport(UIKit)
import CryptoKit
import PencilKit
import PersonalizedNotesKit
import UIKit

enum PencilKitInkAdapter {
    static func makeBundle(
        drawing: PKDrawing,
        pageID: UUID,
        width: Double,
        height: Double
    ) throws -> InkBundle {
        var hasPressure = false
        var hasAzimuth = false
        var hasAltitude = false
        var hasTiming = false

        let strokes = drawing.strokes.enumerated().compactMap { index, stroke -> InkStroke? in
            let path = stroke.path
            guard path.count > 0 else { return nil }
            var points: [InkPoint] = []
            points.reserveCapacity(path.count)
            var totalWidth = 0.0
            for pointIndex in 0..<path.count {
                let point = path[pointIndex]
                let force = Double(point.force)
                let azimuth = Double(point.azimuth)
                let altitude = Double(point.altitude)
                hasPressure = true
                hasAzimuth = true
                hasAltitude = true
                hasTiming = true
                totalWidth += max(Double(point.size.width), 0.1)
                points.append(
                    InkPoint(
                        x: Double(point.location.x),
                        y: Double(point.location.y),
                        timeOffset: max(0, point.timeOffset),
                        pressure: force,
                        azimuth: azimuth,
                        altitude: altitude
                    )
                )
            }

            var red: CGFloat = 0
            var green: CGFloat = 0
            var blue: CGFloat = 0
            var alpha: CGFloat = 1
            _ = stroke.ink.color.getRed(&red, green: &green, blue: &blue, alpha: &alpha)
            let inkType = String(describing: stroke.ink.inkType).lowercased()
            let tool: InkTool
            if inkType.contains("pencil") {
                tool = .pencil
            } else if inkType.contains("marker") {
                tool = .marker
            } else if inkType.contains("crayon") {
                tool = .crayon
            } else if inkType.contains("pen") || inkType.contains("monoline") || inkType.contains("fountain") {
                tool = .pen
            } else {
                tool = .unknown
            }

            let transform = stroke.transform
            return InkStroke(
                strokeID: stableStrokeID(pageID: pageID, index: index),
                points: points,
                tool: tool,
                color: InkRGBA(red: Double(red), green: Double(green), blue: Double(blue), alpha: Double(alpha)),
                width: max(totalWidth / Double(points.count), 0.1),
                transform: InkAffineTransform(
                    a: Double(transform.a), b: Double(transform.b), c: Double(transform.c),
                    d: Double(transform.d), tx: Double(transform.tx), ty: Double(transform.ty)
                )
            )
        }

        let nativeDrawing = drawing.dataRepresentation().isEmpty
            ? nil
            : NativeDrawing(data: drawing.dataRepresentation())
        return InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: width, height: height),
            strokes: strokes,
            capture: InkCaptureCapabilities(
                pressure: hasPressure,
                azimuth: hasAzimuth,
                altitude: hasAltitude,
                timing: hasTiming,
                source: "apple_pencil"
            ),
            nativeDrawing: nativeDrawing
        )
    }

    private static func stableStrokeID(pageID: UUID, index: Int) -> UUID {
        let digest = SHA256.hash(data: Data("\(pageID.uuidString.lowercased()):\(index)".utf8))
        let bytes = Array(digest.prefix(16))
        return UUID(uuid: (
            bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
        ))
    }
}
#endif
