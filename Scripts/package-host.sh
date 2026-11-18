#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source Scripts/build-environment.sh
: "${DUO_DEVELOPMENT_TEAM:?Set your Apple Developer team ID.}"
: "${DUO_HOST_BUNDLE_ID:?Set your owned Mac app bundle identifier.}"
: "${DUO_DEVELOPER_ID:?Set your Developer ID Application signing identity.}"
profile="${DUO_DEVELOPER_ID_PROFILE:-}"
entitlements=""
if [[ -n "$profile" ]]; then
  entitlements=Apps/macOS/DuoWorkspaceHost.entitlements
fi
output="$DUO_BUILD_ROOT/distribution"
mkdir -p "$output"
xcodebuild -project DuoWorkspace.xcodeproj -scheme DuoWorkspaceHost -configuration Release \
  -destination 'generic/platform=macOS' -archivePath "$output/DuoWorkspaceHost.xcarchive" \
  -derivedDataPath "$output/DerivedData" DEVELOPMENT_TEAM="$DUO_DEVELOPMENT_TEAM" \
  PRODUCT_BUNDLE_IDENTIFIER="$DUO_HOST_BUNDLE_ID" CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="$DUO_DEVELOPER_ID" CODE_SIGNING_ALLOWED=YES DUO_LOCAL_SIGNING=NO \
  CODE_SIGN_ENTITLEMENTS="$entitlements" PROVISIONING_PROFILE_SPECIFIER="$profile" PROVISIONING_PROFILE= \
  ENABLE_HARDENED_RUNTIME=YES OTHER_CODE_SIGN_FLAGS='--timestamp' archive
app="$output/DuoWorkspaceHost.xcarchive/Products/Applications/DuoWorkspaceHost.app"
codesign --verify --deep --strict "$app"
/usr/bin/python3 Scripts/verify-host-native-touch-signing.py "$app" "$DUO_DEVELOPMENT_TEAM" "$DUO_HOST_BUNDLE_ID" "$profile"
ditto -c -k --keepParent "$app" "$output/DuoWorkspaceHost.zip"
if [[ -n "${DUO_NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$output/DuoWorkspaceHost.zip" --keychain-profile "$DUO_NOTARY_PROFILE" --wait
  xcrun stapler staple "$app"
  xcrun stapler validate "$app"
  spctl --assess --type execute "$app"
  ditto -c -k --keepParent "$app" "$output/DuoWorkspaceHost.zip"
fi
shasum -a 256 "$output/DuoWorkspaceHost.zip" > "$output/SHA256SUMS"
echo "Distribution artifacts: $output"
