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

    /// Full OAuth callback URL (e.g. cali://oauth/google). Must have a compiled
    /// default: custom INFOPLIST_KEY_* build settings never reach the generated
    /// Info.plist, and scheme env vars vanish once the app is relaunched outside Xcode.
    static var googleOAuthCallbackURL: URL {
        let string = stringValue(infoKey: "GoogleOAuthCallbackURL", envKey: "GOOGLE_OAUTH_CALLBACK_URL")
            ?? "cali://oauth/google"
        return URL(string: string) ?? URL(string: "cali://oauth/google")!
    }

    /// Callback URL scheme used by ASWebAuthenticationSession.
    static var googleOAuthCallbackScheme: String {
        googleOAuthCallbackURL.scheme ?? "cali"
    }

    /// Local backend the simulator talks to (`docker compose up backend` or uvicorn on :8000).
    private static let simulatorBackendURL = "http://localhost:8000"

    /// API origin. Simulator builds default to the local backend; device and
    /// TestFlight builds use the hosted API. Scheme env vars (BACKEND_URL /
    /// AGENT_BASE_URL) still win when enabled, but they only exist when Xcode
    /// launches the app, so the compiled defaults must stand on their own.
    static var backendURL: URL {
        var candidates = [
            environmentValue(for: "BACKEND_URL"),
            environmentValue(for: "AGENT_BASE_URL"),
        ]
        #if targetEnvironment(simulator)
        candidates.append(simulatorBackendURL)
        #endif
        candidates += [
            infoValue(for: "BackendURL"),
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
