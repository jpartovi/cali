//
//  NetworkSession.swift
//  Cali
//
//  Shared URLSession configuration to reduce network stack warnings
//

import Foundation

/// Centralized URLSession configuration for the app.
/// Optimized to reduce verbose network stack warnings.
final class NetworkSession {
    /// Shared URLSession instance with optimized configuration
    static let shared: URLSession = {
        let configuration = URLSessionConfiguration.default
        
        // Connection settings to reduce verbose warnings
        configuration.waitsForConnectivity = false  // Don't wait, fail fast
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60
        
        // Cache configuration
        configuration.urlCache = URLCache(
            memoryCapacity: 4 * 1024 * 1024,  // 4 MB memory cache
            diskCapacity: 20 * 1024 * 1024    // 20 MB disk cache
        )
        configuration.requestCachePolicy = .useProtocolCachePolicy
        
        // HTTP settings
        configuration.httpShouldSetCookies = true
        // Note: httpShouldUsePipelining deprecated in iOS 18.4, removed for compatibility
        configuration.httpMaximumConnectionsPerHost = 4
        
        // Network service type
        configuration.networkServiceType = .default
        
        return URLSession(configuration: configuration)
    }()
}
