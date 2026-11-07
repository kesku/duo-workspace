#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source Scripts/build-environment.sh
duo_build_root="$DUO_BUILD_ROOT"
duo_host_app="$duo_build_root/mac/Build/Products/Debug/DuoWorkspaceHost.app"
Scripts/setup-local-signing.sh "$duo_host_app"
DUO_BUILD_ROOT="$duo_build_root" Scripts/test-microphone-driver.sh
python3 Scripts/test-microphone-package.py
swift test --scratch-path "$duo_build_root/swift"
xcodebuild -project DuoWorkspace.xcodeproj -scheme DuoWorkspaceHost -destination 'platform=macOS' -derivedDataPath "$duo_build_root/mac" CODE_SIGNING_ALLOWED=NO build
/usr/bin/python3 Scripts/local_signing.py verify --state-dir "${DUO_SIGNING_DIR:-$PWD/.secrets}" --app "$duo_host_app"
if [[ -n "${DUO_TEST_DESTINATION:-}" ]]; then
    xcodebuild -project DuoWorkspace.xcodeproj -scheme DuoWorkspace -destination "$DUO_TEST_DESTINATION" -derivedDataPath "$duo_build_root/ios" CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- test
else
    xcodebuild -project DuoWorkspace.xcodeproj -scheme DuoWorkspace -destination 'generic/platform=iOS Simulator' -derivedDataPath "$duo_build_root/ios" CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- build
fi
