import SwiftUI
import PersonalizedNotesKit
#if canImport(UIKit)
import UIKit
#endif

@main
struct PersonalizedNotesApp: App {
    @StateObject private var model: AppModel

    init() {
        #if DEBUG && canImport(UIKit)
        if OfflineStoreBootstrap.isUITesting {
            UIView.setAnimationsEnabled(false)
        }
        #endif
        _model = StateObject(wrappedValue: AppModel.bootstrap())
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}
