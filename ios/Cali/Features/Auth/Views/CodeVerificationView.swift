//
//  CodeVerificationView.swift
//  Cali
//
//  Created by GPT-5 Codex on 11/12/25.
//

import SwiftUI

struct CodeVerificationView: View {
    @EnvironmentObject private var viewModel: AuthViewModel
    let focusedField: FocusState<ContentView.Field?>.Binding

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            Text("Enter Verification Code")
                .font(.system(size: 32, weight: .bold, design: .rounded))
                .foregroundStyle(ColorPalette.Text.primary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)

            VStack(alignment: .leading, spacing: 12) {
                Text("Code")
                    .font(.headline)
                    .foregroundStyle(ColorPalette.Text.secondary)

                OTPCodeField(code: $viewModel.otpCode, focusedField: focusedField)
                    .onChange(of: viewModel.otpCode) { _, newValue in
                        if newValue.filter(\.isNumber).count == OTPCodeField.length, !viewModel.isLoading {
                            Task {
                                await viewModel.verifyOTP()
                            }
                        }
                    }
            }
            .padding(.horizontal, 32)

            Button {
                Task {
                    await viewModel.verifyOTP()
                }
            } label: {
                if viewModel.isLoading {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(ColorPalette.Text.inverted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 18)
                } else {
                    Text("Verify & Continue")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(ColorPalette.Text.inverted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 18)
                }
            }
            .buttonStyle(.plain)
            .background(ColorPalette.Gradients.primary)
            .clipShape(Capsule())
            .shadow(
                color: ColorPalette.Semantic.primary.opacity(0.35),
                radius: 24,
                x: 0,
                y: 14
            )
            .padding(.horizontal, 40)
            .disabled(viewModel.isLoading)

            Spacer()
        }
        .frame(maxWidth: 420)
        .padding()
        .contentShape(Rectangle())
        .onTapGesture {
            focusedField.wrappedValue = nil
        }
    }
}

/// Six digit boxes backed by a single invisible text field, so keyboard entry,
/// paste, and one-time-code autofill all behave like a normal field.
private struct OTPCodeField: View {
    static let length = 6

    @Binding var code: String
    let focusedField: FocusState<ContentView.Field?>.Binding

    private var isFocused: Bool { focusedField.wrappedValue == .code }

    var body: some View {
        ZStack {
            TextField("", text: $code)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .foregroundStyle(.clear)
                .tint(.clear)
                .focused(focusedField, equals: .code)
                .onChange(of: code) { _, newValue in
                    let digits = String(newValue.filter(\.isNumber).prefix(Self.length))
                    if digits != newValue {
                        code = digits
                    }
                }

            HStack(spacing: 10) {
                ForEach(0..<Self.length, id: \.self) { index in
                    digitBox(at: index)
                }
            }
            .allowsHitTesting(false)
        }
        .frame(height: 60)
    }

    private func digitBox(at index: Int) -> some View {
        let digits = Array(code)
        let isActive = isFocused && index == min(digits.count, Self.length - 1)

        return Text(index < digits.count ? String(digits[index]) : "")
            .font(.system(size: 28, weight: .semibold, design: .rounded))
            .monospacedDigit()
            .foregroundStyle(ColorPalette.Text.primary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(
                        isActive ? ColorPalette.Semantic.primary : Color.clear,
                        lineWidth: 2
                    )
            )
            .animation(.easeOut(duration: 0.15), value: isActive)
    }
}
