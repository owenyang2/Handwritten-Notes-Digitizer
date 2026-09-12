import SwiftUI
import PersonalizedNotesKit

@main
struct PersonalizedNotesApp: App {
    @StateObject private var model: AppModel

    init() {
        _model = StateObject(wrappedValue: AppModel.bootstrap())
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}
