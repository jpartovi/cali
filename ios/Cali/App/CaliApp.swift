//
//  CaliApp.swift
//  Cali
//
//  Created by Jude Partovi on 11/8/25.
//

import SwiftUI

@main
struct CaliApp: App {
    init() {
        // Configure log suppression at app startup
        // This reduces verbose system warnings in development/Simulator builds
        LogSuppression.configure()
        print("Cali backend URL: \(AppConfiguration.backendURL.absoluteString)")
        print("Cali supabase URL: \(AppConfiguration.supabaseURL)")
    }
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .onOpenURL { url in
                    CameraLaunch.shared.handle(url)
                }
        }
    }
}
