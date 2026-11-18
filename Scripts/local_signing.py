#!/usr/bin/env python3
"""Create and reuse the Mac host's local code-signing identity."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time


class SigningError(Exception):
    pass


def run(arguments, input_data=None, sensitive=False):
    try:
        result = subprocess.run(arguments, input=input_data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=45, check=False)
    except subprocess.TimeoutExpired:
        raise SigningError(f"{Path(arguments[0]).name} timed out. Signing files were kept.") from None
    if result.returncode:
        detail = "" if sensitive else result.stderr.decode("utf-8", errors="replace").strip()[:1500]
        raise SigningError(f"{Path(arguments[0]).name} failed ({result.returncode}). {detail}".rstrip())
    return result.stdout


def security(*arguments):
    # security -i parses its own quotes. Pass secrets through stdin, not arguments.
    quoted = []
    for argument in arguments:
        value = str(argument)
        if any(character in value for character in "\r\n\0"):
            raise SigningError("Signing paths and passwords must not contain line breaks or NUL bytes.")
        quoted.append('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"')
    command = (" ".join(quoted) + "\n").encode("utf-8")
    if len(command) >= 4000:
        raise SigningError("The signing path is too long for security's command reader.")
    return run(["/usr/bin/security", "-q", "-i"], input_data=command, sensitive=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def read_owned_file(path, limit=65536):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SigningError(f"Expected an owned regular file: {path}")
        data = source.read(limit + 1)
        if len(data) > limit:
            raise SigningError(f"Signing file is too large: {path.name}")
        return data


@contextlib.contextmanager
def locked_state(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise SigningError("The signing directory must be an owned directory, not a symbolic link.")
    directory.chmod(0o700)
    descriptor = os.open(directory / ".setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SigningError("The signing lock must be an owned regular file.")
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SigningError("Another signing operation is still running. Try again when it finishes.") from None
                time.sleep(0.1)
        yield
    finally:
        os.close(descriptor)


class LocalSigner:
    def __init__(self, directory):
        self.directory = Path(os.path.abspath(directory))
        self.keychain = self.directory / "duo-signing.keychain-db"
        self.certificate = self.directory / "development.crt"
        self.password_file = self.directory / "signing-password"
        self.manifest = self.directory / "signing-state.json"
        self.pending = self.directory / ".signing-in-progress"
        self.password = None
        self.fingerprint = None

    def ensure(self, guard_app=None):
        required = [self.keychain, self.certificate, self.password_file]
        if self.pending.exists():
            raise SigningError(f"Local signing setup was interrupted. Preserve and inspect {self.directory}; no identity was replaced.")
        present = [path.exists() or path.is_symlink() for path in required]
        if not any(present):
            legacy = [self.manifest, self.directory / "development.key", self.directory / "development.p12"]
            if any(path.exists() or path.is_symlink() for path in legacy):
                raise SigningError("Local signing files are incomplete. Restore the original signing directory; do not create a replacement identity.")
            if guard_app and self.signed_certificate(guard_app) is not None:
                raise SigningError("A signed host already exists but its signing files are missing. Restore the original signing directory before rebuilding.")
            self.create()
        elif not all(present):
            raise SigningError("Local signing files are incomplete. Restore the original signing directory; no new identity was created.")
        certificate, password_data = self.validate_files()
        if guard_app:
            previous = self.signed_certificate(guard_app)
            if previous is not None and digest(previous) != digest(certificate):
                raise SigningError("The existing host uses a different signing identity. Restore its signing directory or use a separate build output.")
        security("unlock-keychain", "-p", self.password, self.keychain)
        identities = security("find-identity", "-p", "codesigning", self.keychain)
        if self.fingerprint.encode() not in identities.upper():
            raise SigningError("The certificate has no matching private key in the configured keychain. Restore the original signing files.")
        # Verify private-key access even when a legacy setup has no manifest.
        with tempfile.TemporaryDirectory(prefix=".signing-check-", dir=self.directory) as temporary:
            probe = Path(temporary) / "signing-probe"
            shutil.copyfile("/usr/bin/true", probe)
            probe.chmod(0o700)
            self.sign_code(probe)
            self.verify(probe)
        expected = {"version": 1, "certificateSHA256": digest(certificate),
                    "passwordSHA256": digest(password_data)}
        if not self.manifest.exists():
            write_private(self.manifest, (json.dumps(expected, sort_keys=True) + "\n").encode())
        return self.fingerprint

    def validate_files(self):
        # Reject symlinks before reading signing material.
        read_owned_file(self.keychain, limit=32 * 1024 * 1024)
        read_owned_file(self.certificate)
        password_data = read_owned_file(self.password_file, limit=1024).rstrip(b"\r\n")
        if not password_data or b"\0" in password_data or b"\n" in password_data or b"\r" in password_data:
            raise SigningError("The local signing password file is invalid. Restore it from the original setup.")
        try:
            self.password = password_data.decode("utf-8")
        except UnicodeDecodeError:
            raise SigningError("The local signing password file is invalid.") from None
        certificate = run(["/usr/bin/openssl", "x509", "-in", str(self.certificate), "-outform", "DER"])
        try:
            run(["/usr/bin/openssl", "x509", "-in", str(self.certificate), "-checkend", "0", "-noout"])
        except SigningError:
            raise SigningError("The local signing certificate has expired. Preserve the signing folder; replacing the identity requires new macOS permissions.") from None
        self.fingerprint = hashlib.sha1(certificate).hexdigest().upper()
        if self.manifest.exists() or self.manifest.is_symlink():
            try:
                saved = json.loads(read_owned_file(self.manifest))
            except (ValueError, UnicodeDecodeError):
                raise SigningError("The signing manifest is damaged. Restore the original signing directory.") from None
            expected = {"version": 1, "certificateSHA256": digest(certificate), "passwordSHA256": digest(password_data)}
            if saved != expected:
                raise SigningError("Local signing files no longer match their saved identity. Restore the originals; no replacement was created.")
        for path in [self.keychain, self.certificate, self.password_file]:
            path.chmod(0o600)
        return certificate, password_data

    def create(self):
        write_private(self.pending, b"Local signing setup has not finished.\n")
        password = secrets.token_hex(32)
        write_private(self.password_file, (password + "\n").encode())
        # Keep the marker on failure so a retry cannot replace an already used key.
        with tempfile.TemporaryDirectory(prefix=".signing-create-", dir=self.directory) as temporary:
            work = Path(temporary)
            key, package, config = work / "key.pem", work / "identity.p12", work / "certificate.cnf"
            name = "Duo Workspace Local " + secrets.token_hex(8)
            config.write_text("[req]\nprompt=no\ndistinguished_name=subject\nx509_extensions=signing\n"
                              f"[subject]\nCN={name}\n[signing]\nbasicConstraints=critical,CA:false\n"
                              "keyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n"
                              "subjectKeyIdentifier=hash\n")
            run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
                 "-days", "3650", "-config", str(config), "-keyout", str(key), "-out", str(self.certificate)])
            key.chmod(0o600)
            run(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(self.certificate),
                 "-name", name, "-out", str(package), "-passout", "file:" + str(self.password_file)])
            security("create-keychain", "-p", password, self.keychain)
            security("set-keychain-settings", "-l", "-u", "-t", "300", self.keychain)
            security("import", package, "-k", self.keychain, "-f", "pkcs12", "-P", password,
                     "-x", "-T", "/usr/bin/codesign")
            security("set-key-partition-list", "-S", "apple-tool:,codesign:", "-s", "-k", password, self.keychain)
        self.pending.unlink()
        print("Created a local host-signing identity.")

    def signed_certificate(self, app):
        app = Path(os.path.abspath(app))
        if not app.exists():
            return None
        if app.is_dir():
            executable = app / "Contents/MacOS/DuoWorkspaceHost"
            if not executable.exists():
                if (app / "Contents/_CodeSignature/CodeResources").exists():
                    raise SigningError("The signed host is missing its executable. Preserve its signing files and inspect the build output.")
                return None  # Xcode creates the empty product folder before its pre-build scripts.
            app = executable
        with tempfile.TemporaryDirectory(prefix=".signing-cert-", dir=self.directory) as temporary:
            prefix = str(Path(temporary) / "certificate")
            result = subprocess.run(["/usr/bin/codesign", "-d", "--extract-certificates=" + prefix, str(app)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if result.returncode:
                if b"not signed at all" in result.stderr:
                    return None
                raise SigningError("Couldn't read the existing host's signature. Preserve its signing files and inspect the build output.")
            leaf = Path(prefix + "0")
            return leaf.read_bytes() if leaf.exists() else None  # ad-hoc code has no certificate

    def sign_code(self, path, runtime=False):
        arguments = ["/usr/bin/codesign", "--force", "--timestamp=none", "--keychain", str(self.keychain),
                     "--sign", self.fingerprint]
        if runtime:
            arguments += ["--options", "runtime"]
        run(arguments + [str(path)])

    def verify(self, path):
        requirement = 'certificate leaf = H"' + self.fingerprint + '"'
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", "=" + requirement, os.path.abspath(path)])

    def sign_host(self, app, runtime=False):
        app = Path(os.path.abspath(app))
        if app.is_symlink() or not app.is_dir():
            raise SigningError("Expected a built DuoWorkspaceHost.app directory.")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleExecutable") != "DuoWorkspaceHost":
            raise SigningError("This signing command only accepts the Duo Workspace Host bundle.")
        self.ensure(guard_app=app)
        # Sign nested code explicitly before signing the app bundle.
        nested = sorted(app.rglob("*.dylib"), key=lambda path: len(path.parts), reverse=True)
        frameworks = sorted(app.rglob("*.framework"), key=lambda path: len(path.parts), reverse=True)
        for path in nested + frameworks:
            if path.is_symlink():
                raise SigningError(f"The built host contains a code symlink: {path.name}. Inspect the bundle before signing.")
            self.sign_code(path, runtime=runtime)
        self.sign_code(app, runtime=runtime)
        self.verify(app)
        print("Signed and verified Duo Workspace Host with its saved local identity.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ensure", "sign", "verify"])
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--app")
    parser.add_argument("--runtime", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SigningError("Local host signing requires macOS.")
    os.umask(0o077)
    signer = LocalSigner(args.state_dir)
    with locked_state(signer.directory):
        if args.action in ("sign", "verify"):
            if not args.app:
                raise SigningError("Provide the built host with --app.")
            if args.action == "sign":
                signer.sign_host(args.app, runtime=args.runtime)
            else:
                signer.validate_files()
                signer.verify(args.app)
                print("Verified Duo Workspace Host's saved signing identity.")
        else:
            signer.ensure(guard_app=args.app)
            print("Local host signing is ready.")


if __name__ == "__main__":
    try:
        main()
    except (SigningError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("Local signing: " + str(error), file=sys.stderr)
        sys.exit(1)
