import SwiftUI

struct AuthFlowView: View {
    @EnvironmentObject private var model: AppModel
    @State private var mode: Mode = .welcome
    @State private var username = ""
    @State private var email = ""
    @State private var password = ""
    @State private var confirmation = ""
    @State private var showPassword = false
    @State private var recoveryEmail = ""
    @State private var recoveryToken = ""
    @State private var recoveryPassword = ""
    @State private var recoveryConfirmation = ""
    @State private var recoveryNotice: String?
    @State private var authNotice: String?
    @State private var openingLibrary = false

    private enum Mode: Equatable {
        case welcome
        case signIn
        case register
        case resetRequest
        case resetConfirm
    }

    var body: some View {
        NavigationStack {
            Group {
                switch mode {
                case .welcome:
                    welcome
                case .signIn:
                    credentialsForm(isRegistration: false)
                case .register:
                    credentialsForm(isRegistration: true)
                case .resetRequest:
                    passwordResetRequestForm
                case .resetConfirm:
                    passwordResetConfirmForm
                }
            }
            .navigationTitle(navigationTitle)
            .toolbar {
                if mode != .welcome {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Back") {
                            mode = .welcome
                            model.errorMessage = nil
                        }
                    }
                }
            }
        }
    }

    private var welcome: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let bootstrapError = model.bootstrapError {
                    Label("Local storage is unavailable: \(bootstrapError)", systemImage: "externaldrive.badge.xmark")
                        .foregroundStyle(.red)
                        .font(.callout)
                }
                VStack(alignment: .leading, spacing: 10) {
                    Image(systemName: "note.text.badge.plus")
                        .font(.system(size: 46))
                        .foregroundStyle(.tint)
                        .accessibilityHidden(true)
                    Text("Write first. Sync when you’re ready.")
                        .font(.largeTitle.weight(.semibold))
                    Text("Your notes are saved on this device immediately. Create an account when you want backup, cross-device sync, and handwriting conversion.")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }

                VStack(spacing: 12) {
                    Button("Create Account") { mode = .register }
                        .modifier(NotebookPrimaryAction())
                        .controlSize(.large)
                        .frame(maxWidth: .infinity)
                    Button("Sign In") { mode = .signIn }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .frame(maxWidth: .infinity)
                    Button {
                        openingLibrary = true
                        Task { await model.continueOffline(); openingLibrary = false }
                    } label: {
                        HStack {
                            Label("Continue Offline", systemImage: "ipad.and.iphone")
                            if openingLibrary { ProgressView() }
                        }
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(openingLibrary)
                }
                .accessibilityElement(children: .contain)

                if !model.documents.isEmpty {
                    Text("You have \(model.documents.count) local note\(model.documents.count == 1 ? "" : "s") on this device.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                Text("Sign-in is optional for local writing. Notes stay on this device until you choose to sync.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: 620)
            .padding(32)
            .frame(maxWidth: .infinity)
        }
    }

    private func credentialsForm(isRegistration: Bool) -> some View {
        Form {
            Section {
                TextField("Username", text: $username)
                    .textContentType(.username)
                    .autocorrectionDisabled()
                    #if canImport(UIKit)
                    .textInputAutocapitalization(.never)
                    #endif
                    .submitLabel(isRegistration ? .next : .go)

                if isRegistration {
                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        #if canImport(UIKit)
                        .keyboardType(.emailAddress)
                        #endif
                        .autocorrectionDisabled()
                        #if canImport(UIKit)
                        .textInputAutocapitalization(.never)
                        #endif
                }

                Group {
                    if showPassword {
                        TextField("Password", text: $password)
                            .textContentType(isRegistration ? .newPassword : .password)
                    } else {
                        SecureField("Password", text: $password)
                            .textContentType(isRegistration ? .newPassword : .password)
                    }
                }
                .overlay(alignment: .trailing) {
                    Button(showPassword ? "Hide password" : "Show password") {
                        showPassword.toggle()
                    }
                    .font(.caption)
                    .buttonStyle(.borderless)
                    .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                }

                if isRegistration {
                    SecureField("Confirm password", text: $confirmation)
                        .textContentType(.newPassword)
                }
            } header: {
                Text(isRegistration ? "Create your account" : "Welcome back")
            } footer: {
                Text(isRegistration ? "Use at least 8 characters. Your local notes remain available if registration needs to be retried." : "Your local library remains available if the server is temporarily unavailable.")
            }

            if let error = model.errorMessage, !error.isEmpty {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
            }

            if let authNotice, !authNotice.isEmpty {
                Section {
                    Label(authNotice, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
            }

            Section {
                Button {
                    submit(isRegistration: isRegistration)
                } label: {
                    HStack {
                        if model.isAuthenticating { ProgressView().controlSize(.small) }
                        Text(isRegistration ? "Create Account" : "Sign In")
                    }
                    .frame(maxWidth: .infinity)
                }
                .disabled(!isValid(isRegistration: isRegistration) || model.isAuthenticating)

                Button(isRegistration ? "Already have an account? Sign In" : "Need an account? Create one") {
                    model.errorMessage = nil
                    authNotice = nil
                    mode = isRegistration ? .signIn : .register
                }
                .frame(maxWidth: .infinity)

                if !isRegistration {
                    Button("Forgot password?") {
                        model.errorMessage = nil
                        recoveryEmail = email
                        recoveryNotice = nil
                        mode = .resetRequest
                    }
                    .frame(maxWidth: .infinity)
                }

                Button("Continue Offline") {
                    Task { await model.continueOffline() }
                }
                .frame(maxWidth: .infinity)

                Button("Back to Welcome") {
                    model.errorMessage = nil
                    authNotice = nil
                    mode = .welcome
                }
                .frame(maxWidth: .infinity)
            }
        }
        .onSubmit {
            if isValid(isRegistration: isRegistration) {
                submit(isRegistration: isRegistration)
            }
        }
    }

    private var passwordResetRequestForm: some View {
        Form {
            Section {
                TextField("Email", text: $recoveryEmail)
                    .textContentType(.emailAddress)
                    #if canImport(UIKit)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    #endif
                    .autocorrectionDisabled()
            } header: {
                Text("Recover your account")
            } footer: {
                Text("If an account matches, we’ll send a one-time reset token. The response is intentionally the same for unknown email addresses.")
            }

            if let error = model.errorMessage, !error.isEmpty {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
            }

            Section {
                Button {
                    let email = recoveryEmail.trimmingCharacters(in: .whitespacesAndNewlines)
                    Task {
                        guard let response = await model.requestPasswordReset(email: email) else { return }
                        if let debugToken = response.debugToken, !debugToken.isEmpty {
                            recoveryToken = debugToken
                            recoveryNotice = "Development server token inserted below. Production builds deliver the token through the configured email channel."
                        } else {
                            recoveryNotice = "Check your email for the one-time reset token, then enter it on the next screen."
                        }
                        mode = .resetConfirm
                    }
                } label: {
                    HStack {
                        if model.isRecovering { ProgressView().controlSize(.small) }
                        Text("Send Reset Token")
                    }
                    .frame(maxWidth: .infinity)
                }
                .disabled(!isValidRecoveryEmail || model.isRecovering)

                Button("Back to Sign In") {
                    model.errorMessage = nil
                    mode = .signIn
                }
                .frame(maxWidth: .infinity)
            }
        }
        .onSubmit {
            guard isValidRecoveryEmail else { return }
            let email = recoveryEmail.trimmingCharacters(in: .whitespacesAndNewlines)
            Task {
                guard let response = await model.requestPasswordReset(email: email) else { return }
                if let debugToken = response.debugToken, !debugToken.isEmpty {
                    recoveryToken = debugToken
                    recoveryNotice = "Development server token inserted below. Production builds deliver the token through the configured email channel."
                } else {
                    recoveryNotice = "Check your email for the one-time reset token, then enter it on the next screen."
                }
                mode = .resetConfirm
            }
        }
    }

    private var passwordResetConfirmForm: some View {
        Form {
            Section {
                TextField("Reset token", text: $recoveryToken)
                    .textContentType(.oneTimeCode)
                    .autocorrectionDisabled()
                SecureField("New password", text: $recoveryPassword)
                    .textContentType(.newPassword)
                SecureField("Confirm new password", text: $recoveryConfirmation)
                    .textContentType(.newPassword)
            } header: {
                Text("Choose a new password")
            } footer: {
                Text("The token expires after one hour and can only be used once. Use at least 8 characters.")
            }

            if let recoveryNotice, !recoveryNotice.isEmpty {
                Section {
                    Text(recoveryNotice)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }

            if let error = model.errorMessage, !error.isEmpty {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
            }

            Section {
                Button {
                    Task {
                        let success = await model.confirmPasswordReset(
                            token: recoveryToken.trimmingCharacters(in: .whitespacesAndNewlines),
                            newPassword: recoveryPassword
                        )
                        guard success else { return }
                        email = recoveryEmail
                        password = ""
                        confirmation = ""
                        recoveryPassword = ""
                        recoveryConfirmation = ""
                        recoveryToken = ""
                        authNotice = "Password updated. Sign in with your new password."
                        mode = .signIn
                    }
                } label: {
                    HStack {
                        if model.isRecovering { ProgressView().controlSize(.small) }
                        Text("Update Password")
                    }
                    .frame(maxWidth: .infinity)
                }
                .disabled(!isValidPasswordReset || model.isRecovering)

                Button("Back to Email") {
                    model.errorMessage = nil
                    mode = .resetRequest
                }
                .frame(maxWidth: .infinity)
            }
        }
        .onSubmit {
            guard isValidPasswordReset else { return }
            Task {
                let success = await model.confirmPasswordReset(
                    token: recoveryToken.trimmingCharacters(in: .whitespacesAndNewlines),
                    newPassword: recoveryPassword
                )
                guard success else { return }
                email = recoveryEmail
                recoveryPassword = ""
                recoveryConfirmation = ""
                recoveryToken = ""
                authNotice = "Password updated. Sign in with your new password."
                mode = .signIn
            }
        }
    }

    private var navigationTitle: String {
        switch mode {
        case .welcome: return "Personalized Notes"
        case .signIn: return "Sign In"
        case .register: return "Create Account"
        case .resetRequest: return "Recover Account"
        case .resetConfirm: return "Reset Password"
        }
    }

    private var isValidRecoveryEmail: Bool {
        AuthValidation.validEmail(recoveryEmail)
    }

    private var isValidPasswordReset: Bool {
        AuthValidation.validResetToken(recoveryToken)
            && AuthValidation.matchingPasswords(recoveryPassword, recoveryConfirmation)
    }

    private func isValid(isRegistration: Bool) -> Bool {
        guard AuthValidation.validUsername(username),
              AuthValidation.validPassword(password) else { return false }
        guard !isRegistration else {
            return AuthValidation.validEmail(email)
                && AuthValidation.matchingPasswords(password, confirmation)
        }
        return true
    }

    private func submit(isRegistration: Bool) {
        let normalizedUsername = AuthValidation.normalized(username)
        let normalizedEmail = AuthValidation.normalized(email)
        Task {
            if isRegistration {
                await model.register(username: normalizedUsername, email: normalizedEmail, password: password)
            } else {
                await model.login(username: normalizedUsername, password: password)
            }
            if case .library = model.route {
                password = ""
                confirmation = ""
            }
        }
    }
}
