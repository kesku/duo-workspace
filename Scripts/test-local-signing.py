#!/usr/bin/env python3
"""Exercise real local signing in disposable keychains; never use .secrets."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "Scripts/local_signing.py"
spec = importlib.util.spec_from_file_location("local_signing", HELPER)
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class LocalSigningTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / ".artifacts/signing-tests"
        output.mkdir(parents=True, exist_ok=True)
        self.work = tempfile.TemporaryDirectory(prefix="case-", dir=output)
        self.root = Path(self.work.name)
        self.search_list = subprocess.check_output(["/usr/bin/security", "list-keychains", "-d", "user"])
        self.default_keychain = subprocess.check_output(["/usr/bin/security", "default-keychain", "-d", "user"])

    def tearDown(self):
        for keychain in self.root.rglob("*.keychain-db"):
            if not keychain.is_symlink():
                subprocess.run(["/usr/bin/security", "delete-keychain", str(keychain)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        self.work.cleanup()
        self.assertEqual(self.search_list, subprocess.check_output(["/usr/bin/security", "list-keychains", "-d", "user"]))
        self.assertEqual(self.default_keychain, subprocess.check_output(["/usr/bin/security", "default-keychain", "-d", "user"]))

    def ensure(self, path):
        signer = signing.LocalSigner(path)
        with signing.locked_state(signer.directory):
            signer.ensure()
        return signer

    def fixture_app(self):
        app = self.root / "Build/DuoWorkspaceHost.app"
        executables = app / "Contents/MacOS"
        resources = app / "Contents/Resources"
        executables.mkdir(parents=True)
        resources.mkdir()
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "DuoWorkspaceHost", "CFBundleIdentifier": "local.duoworkspace.host",
            "CFBundlePackageType": "APPL", "CFBundleVersion": "1"}))
        shutil.copyfile("/usr/bin/true", executables / "DuoWorkspaceHost")
        (executables / "DuoWorkspaceHost").chmod(0o700)
        environment = dict(os.environ)
        environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode-27.1.0.app/Contents/Developer")
        subprocess.run(["/usr/bin/xcrun", "clang", "-dynamiclib", "-x", "c", "-", "-o",
                        str(executables / "DuoWorkspaceHost.debug.dylib")],
                       input=b"int signing_fixture(void) { return 1; }\n", env=environment,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        (resources / "version.txt").write_text("first build\n")
        signing.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)])
        return app

    def requirement(self, path):
        return subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True).stdout

    def test_rebuild_preserves_requirement_and_signs_inner_code(self):
        signer = self.ensure(self.root / "identity")
        app = self.fixture_app()
        with signing.locked_state(signer.directory):
            signer.sign_host(app)
        first = self.requirement(app)
        certificate = signer.certificate.read_bytes()
        password_hash = hashlib.sha256(signer.password_file.read_bytes()).digest()
        signer.verify(app / "Contents/MacOS/DuoWorkspaceHost.debug.dylib")
        (app / "Contents/Resources/version.txt").write_text("second build\n")
        with signing.locked_state(signer.directory):
            signer.sign_host(app)
        self.assertEqual(first, self.requirement(app))
        self.assertEqual(certificate, signer.certificate.read_bytes())
        self.assertTrue(password_hash == hashlib.sha256(signer.password_file.read_bytes()).digest())

    def test_local_host_signing_omits_native_touch_entitlement(self):
        signer = self.ensure(self.root / "identity")
        app = self.fixture_app()
        signing.run(["/usr/bin/codesign", "--force", "--timestamp=none", "--keychain", str(signer.keychain),
                     "--sign", signer.fingerprint, "--entitlements", str(ROOT / "Apps/macOS/DuoWorkspaceHost.entitlements"),
                     str(app)])  # Simulate rebuilding over a host signed by the previous local script.
        subprocess.run([str(ROOT / "Scripts/sign-local-host.sh"), str(app)],
                       env={**os.environ, "DUO_SIGNING_DIR": str(signer.directory)},
                       capture_output=True, check=True)
        signer.verify(app)
        result = subprocess.run(["/usr/bin/codesign", "-d", "--entitlements", "-", "--xml", str(app)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.assertNotIn("com.apple.developer.hid.virtual.device", plistlib.loads(result.stdout) if result.stdout else {})

    def test_paths_are_literal_and_passwords_never_enter_arguments(self):
        arguments = []
        original = subprocess.run

        def record(command, *args, **kwargs):
            arguments.append(list(command))
            return original(command, *args, **kwargs)

        path = self.root / 'identity spaces "quote" \'apostrophe\' $(touch escaped) `literal` \\ path'
        with mock.patch.object(signing.subprocess, "run", side_effect=record):
            signer = self.ensure(path)
        password = signer.password_file.read_text().strip()
        self.assertTrue(all(password not in " ".join(command) for command in arguments),
                        "A signing password appeared in a process argument.")
        self.assertFalse((ROOT / "escaped").exists())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for item in [signer.certificate, signer.keychain, signer.password_file, signer.manifest]:
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)

    def test_concurrent_first_runs_create_one_identity(self):
        path = self.root / "shared identity"
        command = ["/usr/bin/python3", str(HELPER), "ensure", "--state-dir", str(path)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        outputs = [process.communicate(timeout=60) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), "Concurrent setup failed.")
        self.assertEqual(sum(stdout.count(b"Created a local") for stdout, _ in outputs), 1)
        self.ensure(path)

    def test_partial_and_interrupted_setup_cannot_replace_material(self):
        path = self.root / "partial"
        path.mkdir()
        certificate = path / "development.crt"
        certificate.write_bytes(b"preserve this existing certificate")
        with self.assertRaises(signing.SigningError):
            self.ensure(path)
        self.assertEqual(certificate.read_bytes(), b"preserve this existing certificate")
        self.assertFalse((path / "duo-signing.keychain-db").exists())
        (path / ".signing-in-progress").write_text("interrupted")
        with self.assertRaises(signing.SigningError):
            self.ensure(path)

    def test_changed_or_missing_password_fails_without_rotation(self):
        signer = self.ensure(self.root / "identity")
        certificate = signer.certificate.read_bytes()
        signer.password_file.write_text("incorrect\n")
        with self.assertRaises(signing.SigningError):
            self.ensure(signer.directory)
        signer.manifest.unlink()  # Legacy setups must also validate the password.
        with self.assertRaises(signing.SigningError):
            self.ensure(signer.directory)
        signer.password_file.unlink()
        with self.assertRaises(signing.SigningError):
            self.ensure(signer.directory)
        self.assertEqual(certificate, signer.certificate.read_bytes())

    def test_mismatched_certificate_cannot_use_another_keychain(self):
        first = self.ensure(self.root / "first")
        second = self.ensure(self.root / "second")
        first.manifest.unlink()  # A legacy setup has no manifest.
        first.certificate.write_bytes(second.certificate.read_bytes())
        with self.assertRaisesRegex(signing.SigningError, "matching private key"):
            self.ensure(first.directory)

    def test_signed_host_blocks_accidental_identity_replacement(self):
        signer = self.ensure(self.root / "original")
        app = self.fixture_app()
        with signing.locked_state(signer.directory):
            signer.sign_host(app)
        replacement = signing.LocalSigner(self.root / "missing-original")
        with signing.locked_state(replacement.directory):
            with self.assertRaisesRegex(signing.SigningError, "signing files are missing"):
                replacement.ensure(guard_app=app)
        self.assertFalse(replacement.certificate.exists())
        self.assertFalse(replacement.password_file.exists())

    def test_certificate_symlink_is_rejected_without_changing_target(self):
        path = self.root / "identity"
        path.mkdir()
        outside = self.root / "keep.crt"
        outside.write_bytes(b"keep")
        (path / "development.crt").symlink_to(outside)
        (path / "duo-signing.keychain-db").write_bytes(b"placeholder")
        (path / "signing-password").write_text("placeholder")
        with self.assertRaises(OSError):
            self.ensure(path)
        self.assertEqual(outside.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
