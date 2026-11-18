#!/usr/bin/env python3
"""Check the native touch entitlement before packaging a Developer ID build."""

from datetime import datetime, timezone
from pathlib import Path
import plistlib
import subprocess
import sys


ENTITLEMENT = "com.apple.developer.hid.virtual.device"


def verify(app, team_id, bundle_id, profile_name):
    signature = subprocess.check_output(["/usr/bin/codesign", "-d", "--entitlements", "-", "--xml", str(app)])
    claims = plistlib.loads(signature) if signature else {}
    if not profile_name:
        if ENTITLEMENT in claims:
            raise ValueError("The host claims native touch without a selected Developer ID profile.")
        return
    if claims.get(ENTITLEMENT) is not True:
        raise ValueError("The host signature does not claim the native touch entitlement.")

    embedded = app / "Contents/embedded.provisionprofile"
    if not embedded.is_file():
        raise ValueError("The host is missing its embedded Developer ID provisioning profile.")
    profile = plistlib.loads(subprocess.check_output(["/usr/bin/security", "cms", "-D", "-i", str(embedded)]))
    if profile.get("Name") != profile_name or team_id not in profile.get("TeamIdentifier", []):
        raise ValueError("The embedded provisioning profile does not match the selected profile and team.")
    grant = profile.get("Entitlements", {})
    app_id = grant.get("com.apple.application-identifier", "")
    if app_id.partition(".")[2] != bundle_id:
        raise ValueError("The embedded provisioning profile does not match the host bundle ID.")
    if profile.get("ProvisionsAllDevices") is not True or grant.get("get-task-allow") is True:
        raise ValueError("Native touch distribution requires a Developer ID profile for all devices.")
    if grant.get(ENTITLEMENT) is not True:
        raise ValueError("The embedded Developer ID profile does not authorize native touch.")
    expires = profile.get("ExpirationDate")
    if not isinstance(expires, datetime) or expires.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("The embedded Developer ID profile has expired or has no expiration date.")


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]), *sys.argv[2:])
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Host signing check failed: {error}")
