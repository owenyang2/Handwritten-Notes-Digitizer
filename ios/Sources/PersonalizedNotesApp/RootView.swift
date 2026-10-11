import PersonalizedNotesKit
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        Group {
            switch model.route {
            case .loading:
                ProgressView("Opening local library…")
                    .padding()
            case .welcome:
                AuthFlowView()
            case .library:
                NavigationStack {
                    LibraryView()
                }
            case .notebook(let documentID):
                NavigationStack {
                    if model.document(id: documentID) != nil {
                        NotebookEditorView(
                            documentID: documentID,
                            initialPageID: model.requestedPageID
                        )
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

}
