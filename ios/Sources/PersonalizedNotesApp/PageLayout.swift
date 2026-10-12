import Foundation

enum PageSizePreset: String, CaseIterable, Identifiable, Sendable {
    case ipadPortrait
    case ipadLandscape
    case letterPortrait
    case letterLandscape
    case a4Portrait
    case a4Landscape
    case legalPortrait
    case legalLandscape

    var id: String { rawValue }

    var title: String {
        switch self {
        case .ipadPortrait: return "iPad portrait"
        case .ipadLandscape: return "iPad landscape"
        case .letterPortrait: return "US Letter portrait"
        case .letterLandscape: return "US Letter landscape"
        case .a4Portrait: return "A4 portrait"
        case .a4Landscape: return "A4 landscape"
        case .legalPortrait: return "US Legal portrait"
        case .legalLandscape: return "US Legal landscape"
        }
    }

    var width: Int {
        switch self {
        case .ipadPortrait, .ipadLandscape: return isLandscape ? 1024 : 768
        case .letterPortrait, .letterLandscape: return isLandscape ? 792 : 612
        case .a4Portrait, .a4Landscape: return isLandscape ? 842 : 595
        case .legalPortrait, .legalLandscape: return isLandscape ? 1008 : 612
        }
    }

    var height: Int {
        switch self {
        case .ipadPortrait, .ipadLandscape: return isLandscape ? 768 : 1024
        case .letterPortrait, .letterLandscape: return isLandscape ? 612 : 792
        case .a4Portrait, .a4Landscape: return isLandscape ? 595 : 842
        case .legalPortrait, .legalLandscape: return isLandscape ? 612 : 1008
        }
    }

    private var isLandscape: Bool {
        rawValue.hasSuffix("Landscape") || rawValue.hasSuffix("landscape")
    }
}
