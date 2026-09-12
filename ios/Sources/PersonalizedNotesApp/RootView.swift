import PersonalizedNotesKit
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var username = ""
    @State private var password = ""

    var body: some View {
        Group {
            switch model.route {
            case .loading:
                ProgressView("Opening local library…")
                    .padding()
            case .login:
                loginView
            case .library:
                NavigationStack {
                    LibraryView()
                }
            case .notebook(let documentID):
                NavigationStack {
                    if let document = model.document(id: documentID) {
                        NotebookCanvasView(document: document)
                    } else {
                        ContentUnavailableView(
                            "Notebook unavailable",
                            systemImage: "note.text",
                            description: Text("The local library no longer contains this notebook.")
                        )
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) {
                                Button("Library") { model.closeNotebook() }
                            }
                        }
                    }
                }
            }
        }
        .alert(
            "Something went wrong",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
        .task {
            await model.start()
        }
    }

    private var loginView: some View {
        NavigationStack {
            Form {
                if let bootstrapError = model.bootstrapError {
                    Section("Local store") {
                        Text(bootstrapError)
                            .foregroundStyle(.red)
                    }
                }
                Section("Account") {
                    TextField("Username", text: $username)
                        .textContentType(.username)
                        #if canImport(UIKit)
                        .textInputAutocapitalization(.never)
                        #endif
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                        .textContentType(.password)
                }
                Section {
                    Button("Sign In") {
                        Task { await model.login(username: username, password: password) }
                    }
                    .disabled(username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || password.isEmpty)
                } footer: {
                    Text("The library is local-first. Sign-in is only required to sync and manage sessions. Default API host is \(OfflineStoreBootstrap.backendURL().absoluteString).")
                }
            }
            .navigationTitle("Personalized Notes")
        }
    }
}
