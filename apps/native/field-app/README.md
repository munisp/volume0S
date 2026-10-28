# VolumeOS native field applications

Source vertical slices for Android (Kotlin/Compose/AppAuth/WorkManager/SQLCipher) and iOS (SwiftUI/ASWebAuthenticationSession/CryptoKit/SQLCipher). Both use the existing `svc-census-sync` tenant/device API and do not contain OAuth client secrets.

## Security and state

- Authorization Code + PKCE uses the system browser/custom tab. Android stores tokens in Keystore-backed encrypted preferences; iOS uses ThisDeviceOnly Keychain items.
- Census records require a bounded tenant/device/consent reference, lawful basis, current accurate geotag, and an allowed business payload.
- Capture is durably written to a SQLCipher outbox before synchronization. Outboxes are limited to 500 unacknowledged records and 50 MiB; an individual envelope is limited to 1 MiB.
- Uploads resume from the server's missing-index manifest. Each chunk is SHA-256 addressed and idempotent. Only `published` with the matching event ID becomes `ACKNOWLEDGED`; local, uploading, and failed states never imply server persistence.

## Build and test

Android: `./gradlew test assembleDebug` with JDK 17 and Android SDK 35. iOS: `xcodegen generate && xcodebuild test -scheme FieldApp -destination 'platform=iOS Simulator,name=iPhone 16'` with current Xcode and SQLCipher dependency resolution. These commands require their respective toolchains and are not executed by the repository verifier.

## Release gates

Debug compilation and simulator/unit tests are not store delivery. Production requires runtime OIDC/API configuration, signed release artifacts, physical Android/iOS tests for offline/restart/resume/key-loss/location permissions, accessibility and battery/network review, backend staging acceptance, privacy review, and store approval. The `.example.invalid` endpoints are deliberately non-routable configuration placeholders.
