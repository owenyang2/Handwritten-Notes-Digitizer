import Foundation

#if canImport(ImageIO) && canImport(PDFKit) && canImport(UIKit)
import ImageIO
import PDFKit
import UIKit

/// Performs cheap structural checks before the import factory asks UIKit to
/// decode a potentially large raster. This is intentionally separate from
/// `ImportedSourceFactory`: the factory still owns the page model and source
/// metadata, while this boundary owns input validation.
enum ImportSourcePreflight {
    private static let maximumPageDimension: CGFloat = 32_768
    private static let maximumImagePixels: Int64 = 100_000_000

    static func validate(
        sourceURL: URL,
        originalFilename: String,
        uniformTypeIdentifier: String?,
        isPDF: Bool
    ) throws {
        let values = try sourceURL.resourceValues(
            forKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey, .fileSizeKey]
        )
        guard values.isSymbolicLink != true,
              values.isDirectory != true,
              values.isRegularFile == true,
              (values.fileSize ?? 0) > 0 else {
            throw ImportedSourceError.unreadable
        }

        let header = try FileHandle(forReadingFrom: sourceURL).read(upToCount: 32) ?? Data()
        if isPDF {
            guard header.starts(with: Data("%PDF-".utf8)) else {
                throw ImportedSourceError.unreadable
            }
            guard let pdf = PDFDocument(url: sourceURL), pdf.pageCount > 0 else {
                throw ImportedSourceError.unreadable
            }
            for index in 0..<pdf.pageCount {
                guard let page = pdf.page(at: index) else {
                    throw ImportedSourceError.unreadable
                }
                let bounds = page.bounds(for: .mediaBox)
                guard bounds.width.isFinite,
                      bounds.height.isFinite,
                      bounds.width > 0,
                      bounds.height > 0,
                      bounds.width <= maximumPageDimension,
                      bounds.height <= maximumPageDimension else {
                    throw ImportedSourceError.unreadable
                }
            }
            return
        }

        guard isRecognizedImageHeader(header) else {
            throw ImportedSourceError.unsupported
        }
        guard let imageSource = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
              CGImageSourceGetCount(imageSource) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil)
                  as? [String: Any],
              let width = (properties[kCGImagePropertyPixelWidth as String] as? NSNumber)?.int64Value,
              let height = (properties[kCGImagePropertyPixelHeight as String] as? NSNumber)?.int64Value,
              width > 0,
              height > 0,
              width <= Int64(maximumPageDimension),
              height <= Int64(maximumPageDimension),
              width.multipliedReportingOverflow(by: height).overflow == false,
              width * height <= maximumImagePixels else {
            throw ImportedSourceError.unreadable
        }

        // Keep these inputs in the validation contract even when the file's
        // extension/UTI is unusual (for example a HEIC shared as `data`).
        // The decoder below remains the final media-type authority.
        _ = originalFilename
        _ = uniformTypeIdentifier
    }

    private static func isRecognizedImageHeader(_ data: Data) -> Bool {
        data.starts(with: [
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        ])
            || data.starts(with: [0xFF, 0xD8, 0xFF])
            || data.starts(with: Data("GIF8".utf8))
            || data.starts(with: [0x49, 0x49, 0x2A, 0x00])
            || data.starts(with: [0x4D, 0x4D, 0x00, 0x2A])
            || isISOBaseMediaImage(data)
    }

    private static func isISOBaseMediaImage(_ data: Data) -> Bool {
        guard data.count >= 12,
              String(data: data[4..<8], encoding: .ascii) == "ftyp",
              let brand = String(data: data[8..<12], encoding: .ascii) else {
            return false
        }
        return ["heic", "heix", "hevc", "hevx", "mif1", "msf1", "avif"].contains(brand)
    }
}
#endif
