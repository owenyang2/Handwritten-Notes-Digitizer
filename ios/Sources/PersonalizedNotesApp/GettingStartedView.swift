import SwiftUI

struct GettingStartedView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section {
                Text("Personalized Notes is local-first. Your writing is saved on this iPad before sync or recognition is attempted.")
                    .foregroundStyle(.secondary)
            } header: {
                Label("A safe first workflow", systemImage: "checkmark.shield")
            }

            Section("1. Create a notebook") {
                Text("Start with a blank notebook, or use New to import a PDF, image, or native notebook archive.")
                    .foregroundStyle(.secondary)
                Button {
                    dismiss()
                    Task { await model.createNotebook(title: "My First Notebook") }
                } label: {
                    Label("Create My First Notebook", systemImage: "square.and.pencil")
                }
            }

            Section("2. Write and confirm local save") {
                Label("Write with Apple Pencil on the canvas. Touch drawing is available from Tools when you need it.", systemImage: "pencil.tip")
                Label("The status near the notebook title says Saved on iPad when the native drawing and canonical ink commit is durable.", systemImage: "externaldrive.fill")
            }

            Section("3. Add and organize pages") {
                Label("Use the plus button beside Pages to add a page, or Editor actions to duplicate, reorder, rename, and delete pages.", systemImage: "plus.rectangle.on.folder")
                Label("Use page search when a notebook has many titled pages; selecting a result opens that exact page.", systemImage: "magnifyingglass")
            }

            Section("4. Sync when ready") {
                Label("Continue Offline keeps the library on this iPad. Sign in later when you want backup, cross-device sync, or conversion.", systemImage: "icloud.slash")
                Label("Sync and conversion errors stay visible in Review; retrying never replaces the original ink.", systemImage: "arrow.triangle.2.circlepath")
            }
        }
        .navigationTitle("Getting Started")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
    }
}
