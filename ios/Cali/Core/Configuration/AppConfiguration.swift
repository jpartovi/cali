//
//  AppConfiguration.swift
//  Cali
//
//  Created by GPT-5 Codex on 11/9/25.
//

import Combine
import Foundation

enum AppConfiguration {
    private static func infoValue(for key: String) -> String? {
        Bundle.main.object(forInfoDictionaryKey: key) as? String
    }

    private static func environmentValue(for key: String) -> String? {
        ProcessInfo.processInfo.environment[key]
    }

    private static func stringValue(infoKey: String, envKey: String) -> String? {
        let candidates = [
            environmentValue(for: envKey),
            infoValue(for: infoKey),
        ]
        for candidate in candidates {
            let trimmed = candidate?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !trimmed.isEmpty {
                return trimmed
            }
        }
        return nil
    }

    /// Full OAuth callback URL (e.g. cali://oauth/google).
    static var googleOAuthCallbackURL: URL {
        guard let string = infoValue(for: "GoogleOAuthCallbackURL") ?? environmentValue(for: "GOOGLE_OAUTH_CALLBACK_URL"),
              let url = URL(string: string) else {
            fatalError("GoogleOAuthCallbackURL must be set in Info.plist or GOOGLE_OAUTH_CALLBACK_URL environment variable")
        }
        return url
    }

    /// Callback URL scheme used by ASWebAuthenticationSession.
    static var googleOAuthCallbackScheme: String {
        googleOAuthCallbackURL.scheme ?? "cali"
    }

    /// API origin. Scheme env vars only exist when Xcode launches the app;
    /// after a swipe-kill the process starts clean, so the compiled default must
    /// be the hosted API, not localhost.
    static var backendURL: URL {
        let candidates = [
            environmentValue(for: "BACKEND_URL"),
            infoValue(for: "BackendURL"),
            environmentValue(for: "AGENT_BASE_URL"),
            infoValue(for: "AgentBaseURL"),
        ]
        for candidate in candidates {
            guard let value = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty,
                  let url = URL(string: value) else {
                continue
            }
            return url
        }
        return URL(string: "https://cali-api.onrender.com")!
    }

    /// Base URL for calling the Cali agent endpoint.
    static var agentBaseURL: URL { backendURL }
    
    /// Supabase project URL (anon client). Must be in the binary, not only the Xcode scheme.
    static var supabaseURL: String {
        stringValue(infoKey: "SupabaseURL", envKey: "SUPABASE_URL")
            ?? "https://ouuqqsntczwicaeqewun.supabase.co"
    }

    /// Supabase anonymous/public key. Safe to embed in the app; not the service role key.
    static var supabaseAnonKey: String {
        stringValue(infoKey: "SupabaseAnonKey", envKey: "SUPABASE_ANON_KEY")
            ?? "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im91dXFxc250Y3p3aWNhZXFld3VuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjI2MjE0NDcsImV4cCI6MjA3ODE5NzQ0N30.8Cs7UWDcA5zW-PMCF3k3yyiVhj_wEn35R1E7HdTJ-ks"
    }
}
