//
//  EventLiveActivityWidget.swift
//  CaliWidgets
//

import ActivityKit
import SwiftUI
import WidgetKit

struct EventLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: EventActivityAttributes.self) { context in
            EventLockScreenView(title: context.state.title)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Text("cali")
                        .font(.system(.subheadline, design: .rounded, weight: .semibold))
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.title)
                        .font(.system(.headline, design: .rounded, weight: .semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.7)
                }
            } compactLeading: {
                Text("cali")
                    .font(.system(.caption2, design: .rounded, weight: .semibold))
            } compactTrailing: {
                Text(context.state.title)
                    .font(.system(.caption, design: .rounded, weight: .semibold))
                    .lineLimit(1)
            } minimal: {
                Image(systemName: "calendar")
            }
        }
    }
}

private struct EventLockScreenView: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(.headline, design: .rounded, weight: .semibold))
            .lineLimit(2)
            .minimumScaleFactor(0.7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
    }
}
