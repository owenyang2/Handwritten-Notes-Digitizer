import SwiftUI

enum AppAppearance: String, CaseIterable {
    case system, light, dark
    var label: String { rawValue.capitalized }
    var colorScheme: ColorScheme? {
        switch self { case .system: nil; case .light: .light; case .dark: .dark }
    }
}

enum NotebookColors {
    static func canvas(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.098, green: 0.098, blue: 0.098) : Color(red: 0.969, green: 0.969, blue: 0.961)
    }
    static func sidebar(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.125, green: 0.125, blue: 0.125) : Color(red: 0.945, green: 0.941, blue: 0.933)
    }
}

struct NotebookPrimaryAction: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    func body(content: Content) -> some View {
        content
            .buttonStyle(.borderedProminent)
            .tint(colorScheme == .dark ? Color(red: 0.91, green: 0.906, blue: 0.894) : Color(red: 0.153, green: 0.153, blue: 0.145))
            .foregroundStyle(colorScheme == .dark ? Color(red: 0.098, green: 0.098, blue: 0.098) : .white)
    }
}

struct AppSettings: Equatable {
    var appearance: AppAppearance = .system
    var wifiOnlyUploads: Bool
    var trainingConsentAcknowledged: Bool

    private static let appearanceKey = "personalized-notes.appearance"
    private static let wifiOnlyKey = "personalized-notes.wifi-only-uploads"
    private static let consentKey = "personalized-notes.training-consent-acknowledged"

    static func load() -> AppSettings {
        let defaults = UserDefaults.standard
        return AppSettings(
            appearance: AppAppearance(rawValue: defaults.string(forKey: appearanceKey) ?? "") ?? .system,
            wifiOnlyUploads: defaults.object(forKey: wifiOnlyKey) as? Bool ?? true,
            trainingConsentAcknowledged: defaults.bool(forKey: consentKey)
        )
    }

    func save() {
        let defaults = UserDefaults.standard
        defaults.set(appearance.rawValue, forKey: Self.appearanceKey)
        defaults.set(wifiOnlyUploads, forKey: Self.wifiOnlyKey)
        defaults.set(trainingConsentAcknowledged, forKey: Self.consentKey)
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var endpointText = ""

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Appearance", selection: Binding(
                    get: { model.settings.appearance },
                    set: { value in
                        var settings = model.settings
                        settings.appearance = value
                        model.updateSettings(settings)
                    }
                )) {
                    ForEach(AppAppearance.allCases, id: \.self) { appearance in
                        Text(appearance.label).tag(appearance)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("appearance-picker")
                Text("Original pages and ink keep their colors.").font(.callout).foregroundStyle(.secondary)
            }
            Section {
                Toggle("Wi-Fi only uploads", isOn: wifiOnlyBinding)
            } header: {
                Text("Sync")
            } footer: {
                Text("When enabled, pending ink and library operations stay on this device until it is on Wi-Fi, Ethernet, or loopback. Drawing is never blocked by this setting.")
            }

            Section {
                Toggle("I understand training consent", isOn: consentBinding)
            } header: {
                Text("Optional personalization")
            } footer: {
                Text(trainingConsentExplanation)
            }

            Section {
                LabeledContent("Client ID", value: model.clientID)
            } header: {
                Text("Device")
            }

            Section {
                TextField("https://notes.example.com", text: $endpointText)
                    .autocorrectionDisabled()
                    #if canImport(UIKit)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    #endif
                Button("Save API endpoint") {
                    Task {
                        if await model.updateBackendURL(endpointText) {
                            endpointText = OfflineStoreBootstrap.backendURL().absoluteString
                        }
                    }
                }
                .disabled(endpointText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button {
                    Task { await model.testBackendConnection() }
                } label: {
                    HStack {
                        Text("Test connection")
                        Spacer()
                        if case .testing = model.backendConnectionState {
                            ProgressView()
                        }
                    }
                }
                .disabled(endpointText != OfflineStoreBootstrap.backendURL().absoluteString)
                endpointStatus
            } header: {
                Text("Advanced · Server connection")
            } footer: {
                Text("On a physical iPad, use the reachable server address—not 127.0.0.1, which points back to the iPad. Credentials are sent only to the endpoint you choose.")
            }
        }
        .navigationTitle("Settings")
        .onAppear {
            endpointText = OfflineStoreBootstrap.backendURL().absoluteString
        }
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
    }

    @ViewBuilder
    private var endpointStatus: some View {
        switch model.backendConnectionState {
        case .idle:
            EmptyView()
        case .testing:
            Label("Checking server…", systemImage: "network")
                .foregroundStyle(.secondary)
        case let .connected(modelStatus):
            Label(
                modelStatus == "loaded" ? "Connected · handwriting model ready" : "Connected · handwriting model unavailable",
                systemImage: "checkmark.circle.fill"
            )
            .foregroundStyle(modelStatus == "loaded" ? .green : .orange)
        case let .failed(message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
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
