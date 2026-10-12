import Foundation
import PersonalizedNotesKit

/// The durable source associated with one local page. A notebook may contain
/// pages imported from more than one file, so a single document-level source
/// path is not sufficient once the user inserts pages into an existing note.
struct PageSourceDescriptor: Equatable, Sendable {
    let url: URL
    let kind: String
    let sourcePageIndex: Int
    let originalFilename: String?
}

enum PageSourceMetadata {
    static let key = "page_sources"

    static func entry(
        sourceURL: URL,
        sourceKind: String,
        sourcePageIndex: Int,
        originalFilename: String?
    ) -> JSONValue {
        var values: JSONDocument = [
            "path": .string(sourceURL.path),
            "kind": .string(sourceKind),
            "source_page_index": .number(Double(max(0, sourcePageIndex))),
        ]
        if let originalFilename, !originalFilename.isEmpty {
            values["original_filename"] = .string(originalFilename)
        }
        return .object(values)
    }

    static func entries(
        sourceURL: URL,
        sourceKind: String,
        pages: [PageSummary],
        originalFilename: String?
    ) -> JSONValue {
        .object(Dictionary(uniqueKeysWithValues: pages.map { page in
            (
                page.pageID.uuidString,
                entry(
                    sourceURL: sourceURL,
                    sourceKind: sourceKind,
                    sourcePageIndex: page.pageIndex,
                    originalFilename: originalFilename
                )
            )
        }))
    }

    static func descriptor(
        for document: DocumentRecord,
        page: PageSummary
    ) -> PageSourceDescriptor? {
        if case let .object(allEntries)? = document.sourceMetadata?[key],
           case let .object(values)? = allEntries[page.pageID.uuidString],
           case let .string(path)? = values["path"],
           case let .string(kind)? = values["kind"],
           !path.isEmpty,
           !kind.isEmpty {
            return PageSourceDescriptor(
                url: URL(fileURLWithPath: path),
                kind: kind,
                sourcePageIndex: integer(values["source_page_index"]) ?? page.pageIndex,
                originalFilename: string(values["original_filename"])
            )
        }

        guard (document.sourceKind == "pdf_import" || document.sourceKind == "image_import"),
              case let .string(path)? = document.sourceMetadata?["durable_file_path"],
              !path.isEmpty else {
            return nil
        }
        return PageSourceDescriptor(
            url: URL(fileURLWithPath: path),
            kind: document.sourceKind,
            sourcePageIndex: page.pageIndex,
            originalFilename: string(document.sourceMetadata?["original_filename"])
        )
    }

    static func merge(
        _ existing: JSONValue?,
        with additions: JSONValue
    ) -> JSONValue {
        var merged: JSONDocument = [:]
        if case let .object(values)? = existing {
            merged = values
        }
        if case let .object(values) = additions {
            for (pageID, source) in values {
                merged[pageID] = source
            }
        }
        return .object(merged)
    }

    private static func string(_ value: JSONValue?) -> String? {
        guard case let .string(value)? = value else { return nil }
        return value
    }

    private static func integer(_ value: JSONValue?) -> Int? {
        switch value {
        case let .number(number): return Int(number)
        case let .string(string): return Int(string)
        default: return nil
        }
    }
}
