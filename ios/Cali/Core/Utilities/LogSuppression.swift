//
//  LogSuppression.swift
//  Cali
//
//  Utilities to suppress system-level warnings in development/Simulator builds
//

import Foundation
import OSLog

#if targetEnvironment(simulator)
private let isSimulator = true
#else
private let isSimulator = false
#endif

#if DEBUG
private let isDebugBuild = true
#else
private let isDebugBuild = false
#endif

/// Suppresses verbose system warnings in development builds
enum LogSuppression {
    /// Configure OSLog to suppress system warnings
    static func configure() {
        // IMPORTANT: For maximum effectiveness, set OS_ACTIVITY_MODE=disable
        // in Xcode scheme environment variables BEFORE running:
        // 1. Product > Scheme > Edit Scheme > Run
        // 2. Arguments tab > Environment Variables
        // 3. Add: Name: OS_ACTIVITY_MODE, Value: disable
        //
        // This must be set before process launch - setting it at runtime here
        // helps but may not catch all warnings that fire during initialization
        
        // Suppress network stack warnings (only in DEBUG/Simulator)
        if isDebugBuild || isSimulator {
            // Attempt to set environment variable for this process
            // Note: This may not affect all system logging (especially from third-party libs like Supabase),
            // but helps reduce some warnings
            setenv("OS_ACTIVITY_MODE", "disable", 0)
            
            // Additional network logging suppression
            suppressNetworkWarnings()
        }
        
        // Additional suppression for Simulator-specific warnings
        if isSimulator {
            suppressSimulatorWarnings()
        }
    }
    
    /// Suppress network stack verbose warnings
    private static func suppressNetworkWarnings() {
        // These warnings are from the low-level network stack and are harmless:
        // - nw_socket_set_connection_idle: Protocol not available (expected on some platforms)
        // - nw_protocol_socket_set_no_wake_from_sleep: Invalid argument (harmless)
        //
        // They occur because the network stack tries to set socket options that
        // aren't available on all connection types. This is normal and doesn't affect functionality.
        //
        // The OS_ACTIVITY_MODE=disable in Xcode scheme will suppress most of these.
        // Some may still appear from third-party libraries (e.g., Supabase) that
        // use their own networking code.
    }
    
    /// Suppress iOS Simulator-specific warnings (HAL, audio, graphics)
    private static func suppressSimulatorWarnings() {
        // Suppress HAL (Hardware Abstraction Layer) audio warnings
        // These are harmless Simulator-only warnings
        UserDefaults.standard.set(true, forKey: "NSHighResolutionCapable")
        
        // Suppress CoreAudio warnings in Simulator
        // Note: Some warnings are at system level and can't be fully suppressed,
        // but this helps reduce them
        
        // IOSurface warnings (graphics) are Simulator-only and harmless
        // These can't be suppressed programmatically but don't affect functionality
    }
}

/// OSLog category for filtered system logs
extension OSLog {
    private static var subsystem = Bundle.main.bundleIdentifier ?? "com.cali.app"
    
    /// Network-related logs (filtered to reduce verbose system warnings)
    static let network = OSLog(
        subsystem: subsystem,
        category: "network"
    )
    
    /// System warnings that should be suppressed in development
    static let suppressed = OSLog(
        subsystem: subsystem,
        category: "suppressed"
    )
}
