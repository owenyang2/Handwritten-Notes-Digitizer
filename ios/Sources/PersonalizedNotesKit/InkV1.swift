import Foundation
import CryptoKit

public enum InkV1 {
    public static let schema = "ink_v1"
    public static let version = 1
}

public struct InkCoordinateSpace: Codable, Equatable, Hashable, Sendable {
    public let width: Double
    public let height: Double
    public let unit: String
    public let origin: String

    public init(
        width: Double,
        height: Double,
        unit: String = "points",
        origin: String = "top_left"
    ) {
        precondition(width > 0 && height > 0, "Ink coordinate dimensions must be positive")
        self.width = width
        self.height = height
        self.unit = unit
        self.origin = origin
    }
}

public struct InkRGBA: Codable, Equatable, Hashable, Sendable {
    public let red: Double
    public let green: Double
    public let blue: Double
    public let alpha: Double

    public init(red: Double, green: Double, blue: Double, alpha: Double = 1) {
        self.red = Self.clamp(red)
        self.green = Self.clamp(green)
        self.blue = Self.clamp(blue)
        self.alpha = Self.clamp(alpha)
    }

    private static func clamp(_ value: Double) -> Double {
        min(1, max(0, value))
    }
}

public enum InkTool: String, Codable, Hashable, Sendable {
    case pen
    case pencil
    case marker
    case crayon
    case eraser
    case unknown
}

public struct InkAffineTransform: Codable, Equatable, Hashable, Sendable {
    public let a: Double
    public let b: Double
    public let c: Double
    public let d: Double
    public let tx: Double
    public let ty: Double

    public init(
        a: Double = 1,
        b: Double = 0,
        c: Double = 0,
        d: Double = 1,
        tx: Double = 0,
        ty: Double = 0
    ) {
        self.a = a
        self.b = b
        self.c = c
        self.d = d
        self.tx = tx
        self.ty = ty
    }
}

public struct InkPoint: Codable, Equatable, Hashable, Sendable {
    public let x: Double
    public let y: Double
    /// Seconds elapsed from the beginning of the stroke.
    public let timeOffset: Double
    /// Normalized pressure, when the capture device reports it.
    public let pressure: Double?
    /// Radians clockwise from the positive x axis, when available.
    public let azimuth: Double?
    /// Radians above the surface, when available.
    public let altitude: Double?

    public init(
        x: Double,
        y: Double,
        timeOffset: Double,
        pressure: Double? = nil,
        azimuth: Double? = nil,
        altitude: Double? = nil
    ) {
        precondition(timeOffset >= 0, "Stroke point offsets cannot be negative")
        self.x = x
        self.y = y
        self.timeOffset = timeOffset
        self.pressure = pressure.map { min(1, max(0, $0)) }
        self.azimuth = azimuth
        self.altitude = altitude
    }
}

public struct InkStroke: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let strokeID: UUID
    public let points: [InkPoint]
    public let tool: InkTool
    public let color: InkRGBA
    public let width: Double
    public let transform: InkAffineTransform

    public init(
        strokeID: UUID = UUID(),
        points: [InkPoint],
        tool: InkTool = .pen,
        color: InkRGBA = InkRGBA(red: 0, green: 0, blue: 0),
        width: Double = 1,
        transform: InkAffineTransform = InkAffineTransform()
    ) {
        precondition(!points.isEmpty, "An ink stroke must contain at least one point")
        precondition(width > 0, "An ink stroke width must be positive")
        self.strokeID = strokeID
        self.points = points
        self.tool = tool
        self.color = color
        self.width = width
        self.transform = transform
    }

    public var id: UUID { strokeID }
}

public struct InkCaptureCapabilities: Codable, Equatable, Hashable, Sendable {
    public let pressure: Bool
    public let azimuth: Bool
    public let altitude: Bool
    public let timing: Bool
    public let source: String

    public init(
        pressure: Bool = false,
        azimuth: Bool = false,
        altitude: Bool = false,
        timing: Bool = true,
        source: String = "apple_pencil"
    ) {
        self.pressure = pressure
        self.azimuth = azimuth
        self.altitude = altitude
        self.timing = timing
        self.source = source
    }
}

public struct NativeDrawing: Codable, Equatable, Hashable, Sendable {
    public let platform: String
    public let mediaType: String
    public let platformVersion: String?
    public let data: Data

    public init(
        platform: String = "apple.pkdrawing",
        mediaType: String = "application/x-apple-pkdrawing",
        platformVersion: String? = nil,
        data: Data
    ) {
        self.platform = platform
        self.mediaType = mediaType
        self.platformVersion = platformVersion
        self.data = data
    }
}

public struct InkBundle: Codable, Equatable, Hashable, Sendable {
    public let schema: String
    public let schemaVersion: Int
    public let pageID: UUID
    public let coordinateSpace: InkCoordinateSpace
    public let strokes: [InkStroke]
    public let capture: InkCaptureCapabilities
    public let nativeDrawing: NativeDrawing?
    public let contentHash: String

    public init(
        pageID: UUID,
        coordinateSpace: InkCoordinateSpace,
        strokes: [InkStroke],
        capture: InkCaptureCapabilities = InkCaptureCapabilities(),
        nativeDrawing: NativeDrawing? = nil
    ) {
        self.schema = InkV1.schema
        self.schemaVersion = InkV1.version
        self.pageID = pageID
        self.coordinateSpace = coordinateSpace
        self.strokes = strokes
        self.capture = capture
        self.nativeDrawing = nativeDrawing
        self.contentHash = Self.computeHash(
            schema: InkV1.schema,
            schemaVersion: InkV1.version,
            pageID: pageID,
            coordinateSpace: coordinateSpace,
            strokes: strokes,
            capture: capture,
            nativeDrawing: nativeDrawing
        )
    }

    public func validate() throws {
        guard schema == InkV1.schema, schemaVersion == InkV1.version else {
            throw InkCodecError.unsupportedSchema(schema, schemaVersion)
        }
        let expected = Self.computeHash(
            schema: schema,
            schemaVersion: schemaVersion,
            pageID: pageID,
            coordinateSpace: coordinateSpace,
            strokes: strokes,
            capture: capture,
            nativeDrawing: nativeDrawing
        )
        guard expected == contentHash else {
            throw InkCodecError.integrityFailure
        }
    }

    private static func computeHash(
        schema: String,
        schemaVersion: Int,
        pageID: UUID,
        coordinateSpace: InkCoordinateSpace,
        strokes: [InkStroke],
        capture: InkCaptureCapabilities,
        nativeDrawing: NativeDrawing?
    ) -> String {
        var payload: [String: Any] = [
            "schema": schema,
            "schema_version": schemaVersion,
            "page_id": pageID.uuidString.lowercased(),
            "coordinate_space": [
                "width": coordinateSpace.width,
                "height": coordinateSpace.height,
                "unit": coordinateSpace.unit,
                "origin": coordinateSpace.origin,
            ],
            "strokes": strokes.map { stroke -> [String: Any] in
                [
                    "stroke_id": stroke.strokeID.uuidString.lowercased(),
                    "points": stroke.points.map { point -> [String: Any] in
                        var item: [String: Any] = [
                            "x": point.x,
                            "y": point.y,
                            "time_offset": point.timeOffset,
                        ]
                        if let pressure = point.pressure { item["pressure"] = pressure }
                        if let azimuth = point.azimuth { item["azimuth"] = azimuth }
                        if let altitude = point.altitude { item["altitude"] = altitude }
                        return item
                    },
                    "tool": stroke.tool.rawValue,
                    "color": [
                        "red": stroke.color.red,
                        "green": stroke.color.green,
                        "blue": stroke.color.blue,
                        "alpha": stroke.color.alpha,
                    ],
                    "width": stroke.width,
                    "transform": [
                        "a": stroke.transform.a,
                        "b": stroke.transform.b,
                        "c": stroke.transform.c,
                        "d": stroke.transform.d,
                        "tx": stroke.transform.tx,
                        "ty": stroke.transform.ty,
                    ],
                ]
            },
            "capture": [
                "pressure": capture.pressure,
                "azimuth": capture.azimuth,
                "altitude": capture.altitude,
                "timing": capture.timing,
                "source": capture.source,
            ],
        ]
        if let nativeDrawing {
            payload["native_drawing"] = [
                "platform": nativeDrawing.platform,
                "media_type": nativeDrawing.mediaType,
                "platform_version": nativeDrawing.platformVersion as Any,
                "data": nativeDrawing.data.base64EncodedString(),
            ]
        }
        let data = (try? CanonicalJSON.data(from: payload)) ?? Data()
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public enum InkCodecError: Error, Equatable, Sendable {
    case unsupportedSchema(String, Int)
    case integrityFailure
    case invalidNativeDrawing
}

public enum InkV1Codec {
    public static func encode(_ bundle: InkBundle) throws -> Data {
        try bundle.validate()
        return try APIJSON.encoder().encode(bundle)
    }

    public static func decode(_ data: Data) throws -> InkBundle {
        do {
            let bundle = try APIJSON.decoder().decode(InkBundle.self, from: data)
            try bundle.validate()
            return bundle
        } catch let error as InkCodecError {
            throw error
        } catch {
            throw InkCodecError.integrityFailure
        }
    }

    public static func write(_ bundle: InkBundle, to url: URL) throws {
        let data = try encode(bundle)
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUnlessOpen])
        #else
        try data.write(to: url, options: [.atomic])
        #endif
    }
}

#if canImport(PencilKit)
import PencilKit

/// Preserves the native drawing exactly while the app-neutral stroke mapping
/// evolves independently. The mapping layer intentionally never substitutes a
/// raster or a lossy re-render for the source `PKDrawing` bytes.
public enum PencilKitDrawingCodec {
    public static func encode(
        _ drawing: PKDrawing,
        platformVersion: String? = nil
    ) throws -> NativeDrawing {
        let data = drawing.dataRepresentation()
        guard !data.isEmpty else { throw InkCodecError.invalidNativeDrawing }
        return NativeDrawing(platformVersion: platformVersion, data: data)
    }

    public static func decode(_ nativeDrawing: NativeDrawing) throws -> PKDrawing {
        guard nativeDrawing.platform == "apple.pkdrawing" else {
            throw InkCodecError.invalidNativeDrawing
        }
        do {
            return try PKDrawing(data: nativeDrawing.data)
        } catch {
            throw InkCodecError.invalidNativeDrawing
        }
    }
}
#endif
