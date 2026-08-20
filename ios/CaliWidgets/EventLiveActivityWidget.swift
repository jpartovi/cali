//
//  EventLiveActivityWidget.swift
//  CaliWidgets
//

import ActivityKit
import AppIntents
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
                DynamicIslandExpandedRegion(.trailing) {
                    OpenCameraButton()
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
        HStack(spacing: 12) {
            Text(title)
                .font(.system(.headline, design: .rounded, weight: .semibold))
                .lineLimit(2)
                .minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity, alignment: .leading)

            OpenCameraButton()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

private struct OpenCameraButton: View {
    var body: some View {
        Button(intent: OpenCameraIntent()) {
            Image(systemName: "camera.fill")
                .font(.system(size: 16, weight: .semibold))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("Camera")
    }
}
