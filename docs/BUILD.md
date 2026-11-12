# Building SlowBooks Switchboard (sbplive)

The program runs from a checkout with any Python 3.10 or newer and needs
nothing installed. For people who do not have Python it is built with
PyInstaller into one console program per platform, `sbplive` (`sbplive.exe`
on Windows): the program Claude Desktop starts on stdin and stdout, and the
one that runs `setup`, `status`, `disconnect` and `serve-http` in a terminal.

## Build it

PyInstaller is a build-time tool only; the program never imports it. It is
pinned in `requirements-build.txt`.

```
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean packaging/sbplive.spec
```

The result is `dist/sbplive` (`dist/sbplive.exe` on Windows), one file.
PyInstaller builds for the platform it runs on, so build on each. The spec
(`packaging/sbplive.spec`, entry point `packaging/entry.py`) collects nothing
but the package, since it imports only the standard library, and leaves
tkinter and the test modules out. On Linux x86-64 with Python 3.12 the
program is about 8 MB and builds in about ten seconds.

On macOS, setting `SBPLIVE_CODESIGN_IDENTITY` to a signing identity in the
keychain makes PyInstaller sign every binary it collects, and the program,
with the hardened runtime and a secure timestamp, ready for notarization.

## Check a build

```
dist/sbplive --version
dist/sbplive status     # in a fresh home: says it is not set up, exits 1
dist/sbplive setup      # then a question in Claude
```

The frozen program writes its own path into Claude Desktop's configuration
(`"command": "<path to the executable>"`, no arguments), so move it where it
will live **before** running setup; if it moves, run setup again.

## In CI

- `.github/workflows/ci.yml`, on every push to `main` and every pull request:
  the tests on Python 3.10 and 3.13 with nothing installed but pytest (and a
  check that the MCP client is absent, so the contract test skips there),
  then the contract test with the pinned official MCP client from
  `requirements-dev.txt`, which must not skip.
- `.github/workflows/build.yml`, on a `v*` tag and on manual dispatch: the
  program built on `windows-latest`, `macos-14` (Apple silicon) and
  `ubuntu-22.04` (an older glibc, so the Linux build runs on newer systems
  too), each smoke-tested (`--version`, and `status` in an empty home).
  - **Windows:** signed with Azure Trusted Signing, as SlowBooks Pro is
    (the job runs under the `release` environment, whose OIDC token the
    signing account trusts; no secret is stored), then zipped.
  - **macOS:** when the repository has the Developer ID secrets
    (`MACOS_CERT_P12`, `MACOS_CERT_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID`,
    `APPLE_APP_PASSWORD`), signed in a throwaway keychain and notarized with
    `notarytool`; without them, the unsigned program is uploaded. A bare
    executable cannot carry a stapled ticket, so Gatekeeper checks the
    notarization online the first time it runs.
  - **Linux:** a `.tar.gz` (so the program keeps its executable bit).
  - Each platform's `SHA256SUMS.windows`, `SHA256SUMS.macos` or
    `SHA256SUMS.linux` lists its files' SHA-256 digests in the
    `sha256sum -c` format. On a tag, the archives and checksums are attached
    to the GitHub release; the tag must match the version in
    `sbplive/__init__.py`.
