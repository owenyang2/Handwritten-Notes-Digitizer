import SwiftUI

struct AppSettings: Equatable {
    var wifiOnlyUploads: Bool
    var trainingConsentAcknowledged: Bool

    private static let wifiOnlyKey = "personalized-notes.wifi-only-uploads"
    private static let consentKey = "personalized-notes.training-consent-acknowledged"

    static func load() -> AppSettings {
        let defaults = UserDefaults.standard
        return AppSettings(
            wifiOnlyUploads: defaults.object(forKey: wifiOnlyKey) as? Bool ?? true,
            trainingConsentAcknowledged: defaults.bool(forKey: consentKey)
        )
    }

    func save() {
        let defaults = UserDefaults.standard
        defaults.set(wifiOnlyUploads, forKey: Self.wifiOnlyKey)
        defaults.set(trainingConsentAcknowledged, forKey: Self.consentKey)
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            Section {
                Toggle("Wi-Fi only uploads", isOn: wifiOnlyBinding)
            } header: {
                Text("Sync")
            } footer: {
                Text("When enabled, pending ink and library operations stay on this iPad until the device is on Wi-Fi, Ethernet, or loopback. Drawing is never blocked by this setting.")
            }

            Section {
                Toggle("I understand training consent", isOn: consentBinding)
            } header: {
                Text("Training consent")
            } footer: {
                Text(trainingConsentExplanation)
            }

            Section("Device") {
                LabeledContent("Client ID", value: model.clientID)
                LabeledContent("API", value: OfflineStoreBootstrap.backendURL().absoluteString)
            }
        }
        .navigationTitle("Settings")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
    }

    private var wifiOnlyBinding: Binding<Bool> {
        Binding(
            get: { model.settings.wifiOnlyUploads },
            set: { value in
                var settings = model.settings
                settings.wifiOnlyUploads = value
                model.updateSettings(settings)
            }
        )
    }

    private var consentBinding: Binding<Bool> {
        Binding(
            get: { model.settings.trainingConsentAcknowledged },
            set: { value in
                var settings = model.settings
                settings.trainingConsentAcknowledged = value
                model.updateSettings(settings)
            }
        )
    }

    private var trainingConsentExplanation: String {
        """
        Reviewed handwriting crops are not used to train a personal recognizer unless you explicitly consent later, per crop or per export. This toggle only records that you have read that policy. It does not enroll pages, does not share notes with other accounts, and does not replace the per-example consent endpoint on the server.
        """
    }
}
