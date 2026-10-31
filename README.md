# SlideFlow

Hoàn toàn làm bằng AI. Thông cảm ! </br>
SlideFlow is an open-source macOS app that turns YouTube lectures into slide PDFs
and text transcripts. Processing is performed locally whenever possible: temporary
video/audio data is deleted after a successful run, and Whisper models remain on
the user's Mac.

> Giao diện và tài liệu sử dụng trong ứng dụng hiện bằng tiếng Việt.

## Features

- Extract slide changes directly from a YouTube stream without keeping the full video.
- Process single videos or selected items from a playlist in a sequential queue.
- Default to 1080p and automatically use the highest available quality when 1080p is absent.
- Save existing YouTube captions or create a local Whisper transcript from audio.
- Keep a local history of generated PDFs.
- Send the active YouTube video or playlist from the bundled Chrome extension.
- Package the app and extension together in one DMG.

## Install a release

Download `SlideFlow.dmg` from the [latest GitHub release](https://github.com/BuHuDu99/SlideFlow/releases/latest),
open it, and drag `SlideFlow.app` to **Applications**. The DMG also contains the
Chrome extension installer and Vietnamese installation instructions.

The downloadable build is ad-hoc signed for local use and is not Apple-notarized.
macOS may therefore require the verification step documented in
[`prepare_local_app.command`](prepare_local_app.command). The script verifies the
expected bundle before removing quarantine metadata; it does not disable Gatekeeper.

## Stop slide scanning early

**Kết thúc quét** defaults to `00:00:00`, meaning the full video. Enter
hours:minutes or hours:minutes:seconds: `02:10` means two hours and ten
minutes and automatically becomes `02:10:00` on leaving the field or pressing
Enter. For 90 minutes, enter `01:30` or `01:30:00`; for 45 minutes use `00:45`.
Blank fields also normalize to `00:00:00`. **Toàn bộ video** resets to zero.

Each playlist video has its own default-zero field. **Áp dụng mốc kết thúc
cho tất cả** is off by default; when enabled the shared field overrides the
individual fields for selected videos. Toggling it off preserves the individual
values. A zero shared field means scan all selected videos in full. Each queued
job retains its own limit. Limits beyond the actual duration are clamped.

The result reports the scanned duration. This setting applies to slide sampling,
not optional subtitle files or the separate transcription actions. CLI:
`--end-time 02:10` (hours:minutes) or `--end-time 02:10:00`.

## Updates in the app

Starting with 2.8.0, SlideFlow checks the latest stable GitHub release once at
launch. Click the version button at the bottom of the sidebar to check again.
When a newer version is available, click **Cập nhật**, then confirm. SlideFlow
shows a progress window with percentage and downloaded MB, then
downloads the DMG, checks its SHA-256 against GitHub's asset digest, verifies
the bundle identifier, version, arm64 binary and code signature, and restarts
after replacing the app. The window shows download, verification and
installation phases; it reports when the network stops supplying data. Cancel
while downloading/preparing to retain the current app and remove temporary
files. A pending network read may take up to 30 seconds to respond to cancellation.
Active extraction jobs must finish first.

The updater needs write access to the app's containing directory. Install in
`~/Applications` if `/Applications` requires an administrator. A failed download
leaves the current app intact; a failed replacement restores the old app.
Temporary downloads, mounted volumes and staging copies are removed after the
operation. User history, models and generated files stay outside the app bundle.

Devices running 2.7.3 must install 2.8.0 manually once. That older application
does not contain an updater and cannot acquire this feature remotely by itself.
This build supports Apple Silicon Macs only.

To publish a new update, change `APP_VERSION` in `app_version.py`, commit and
push the source, then run `./scripts/publish_release.command` with the packaging
environment installed. It builds and uploads `SlideFlow.dmg` as a stable release
tagged with that version; GitHub supplies the SHA-256 asset digest. Do not reuse
a published version number. Prereleases and drafts are not offered in the app.

## Run from source

Requirements:

- macOS 13 or later on Apple Silicon
- Python 3.12
- Internet access for YouTube and the first Whisper model download

```bash
git clone https://github.com/BuHuDu99/SlideFlow.git
cd SlideFlow
./setup.command
./run.command
```

`setup.command` creates `.venv` and installs the runtime dependencies. `run.command`
also invokes setup automatically when the environment does not exist.

Whisper's model cache is stored outside the repository at:

```text
~/Library/Application Support/SlideFlow/Models
```

Generated PDFs, transcripts, model files, browser cookies, local history, `.venv`,
build output, and DMG files are not committed to this repository.

## Build the DMG

Install the development dependencies and build:

```bash
./setup.command --dev
./build_app.command
```

The result is `dist/SlideFlow.dmg`. The build script signs the local bundle,
verifies the DMG, and removes intermediate build/cache directories.

## Test

```bash
./setup.command --dev
.venv/bin/python -m unittest discover -s tests -v
```

## Chrome extension

The extension sends only the active public YouTube URL and tab title to SlideFlow
through Chrome Native Messaging on the same Mac. It does not read or transmit
cookies, captions, or page contents. See
[`chrome_extension/README.md`](chrome_extension/README.md) for installation details.

## Project layout

- `main.py` — desktop UI and queue/history orchestration
- `online_extractor.py` — online slide extraction
- `transcript_extractor.py` — captions and local Whisper transcription
- `subtitle_proxy.py` — bounded fallback handling for blocked caption requests
- `chrome_extension/` and `chrome_native_host.c` — local Chrome integration
- `SlideFlow.spec` and `build_app.command` — reproducible macOS packaging
- `tests/` — unit and integration tests

## Privacy and responsible use

Use SlideFlow only with content you are permitted to access and process. Browser
cookies are read only when YouTube requires authentication and are not exported to
project files. Public proxy fallbacks never receive browser cookies.

## Credits and license

SlideFlow is derived from the original
[YouTube Slide Extractor](https://github.com/nishant-Tiwari24/cracker) by Nishant
Tiwari and has been substantially redesigned and extended by BuHuDu.

Released under the [MIT License](LICENSE).
