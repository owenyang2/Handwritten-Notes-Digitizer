#if canImport(UIKit)
import UniformTypeIdentifiers
import UIKit

/// Share-sheet entry point. Accepts `public.image` and `com.adobe.pdf` and
/// writes a pending import into the App Group container
/// `group.com.handwrittennotes.digitizer`. It never uploads.
public final class ShareViewController: UIViewController {
    private let statusLabel = UILabel()

    public override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.textAlignment = .center
        statusLabel.numberOfLines = 0
        statusLabel.text = "Saving to Personalized Notes…"
        view.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            statusLabel.leadingAnchor.constraint(equalTo: view.layoutMarginsGuide.leadingAnchor),
            statusLabel.trailingAnchor.constraint(equalTo: view.layoutMarginsGuide.trailingAnchor),
            statusLabel.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
        Task { await importAttachments() }
    }

    private func importAttachments() async {
        guard let items = extensionContext?.inputItems as? [NSExtensionItem] else {
            finish(error: ShareInboxError.invalidPayload)
            return
        }

        var imported = 0
        for item in items {
            guard let providers = item.attachments else { continue }
            for provider in providers {
                do {
                    if let payload = try await loadPayload(from: provider) {
                        _ = try ShareInboxStore.writePendingImport(
                            data: payload.data,
                            filename: payload.filename,
                            uniformTypeIdentifier: payload.uti
                        )
                        imported += 1
                    }
                } catch {
                    finish(error: error)
                    return
                }
            }
        }

        if imported == 0 {
            finish(error: ShareInboxError.invalidPayload)
            return
        }
        await MainActor.run {
            statusLabel.text = imported == 1
                ? "Saved 1 item. Open Personalized Notes to import it."
                : "Saved \(imported) items. Open Personalized Notes to import them."
        }
        extensionContext?.completeRequest(returningItems: nil)
    }

    private struct Payload {
        var data: Data
        var filename: String
        var uti: String
    }

    private func loadPayload(from provider: NSItemProvider) async throws -> Payload? {
        if provider.hasItemConformingToTypeIdentifier(UTType.pdf.identifier) {
            let data = try await loadData(provider, typeIdentifier: UTType.pdf.identifier)
            return Payload(
                data: data,
                filename: provider.suggestedName ?? "import.pdf",
                uti: UTType.pdf.identifier
            )
        }
        if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
            let data = try await loadData(provider, typeIdentifier: UTType.image.identifier)
            return Payload(
                data: data,
                filename: provider.suggestedName ?? "import.image",
                uti: UTType.image.identifier
            )
        }
        return nil
    }

    private func loadData(_ provider: NSItemProvider, typeIdentifier: String) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            provider.loadDataRepresentation(forTypeIdentifier: typeIdentifier) { data, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let data, !data.isEmpty else {
                    continuation.resume(throwing: ShareInboxError.invalidPayload)
                    return
                }
                continuation.resume(returning: data)
            }
        }
    }

    private func finish(error: Error) {
        statusLabel.text = error.localizedDescription
        extensionContext?.cancelRequest(withError: error)
    }
}
#else
/// Share extension UI is iOS-only. The ShareInbox store still compiles on macOS.
public enum ShareExtensionUnavailable {
    public static let requiresIOS = true
}
#endif
