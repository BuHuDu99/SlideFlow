# SlideFlow

Hoàn toàn làm bằng AI. Thông cảm !
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
