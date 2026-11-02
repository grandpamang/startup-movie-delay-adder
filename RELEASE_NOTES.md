# v1.0.0-rc.1

First public release candidate. Adds 1–20 seconds of black and silence to a separate copy of a local startup movie. Six seconds is the default.

This continues the original Startup Movie Delay Adder project. The single `.sh` download and launch command remain; the implementation now uses Python 3.9+ for paths, metadata, and file operations. The original MIT notice is preserved.

Changes from the earlier ZIP:

- Automatic mode uses the default delay without prompting.
- Numeric titles need identification; saved names are keyed by video contents.
- Output creation refuses files, directories, symlinks, and concurrent collisions.
- UTF-8 titles stay valid; control characters and path separators are removed.
- Renamed generated movies are detected through metadata.
- FFmpeg feature checks, restricted input formats/protocols, complete output decoding, and failure cleanup.
- Explicit handling of extra tracks, subtitles, multichannel audio, unsupported HDR/rotation, and offset timestamps.
- Source metadata is removed from the generated copy.
- Test coverage expanded beyond the original nine tests. CI uses pinned actions, read-only permissions, and no secrets.

## Validation

Local Windows development check: Python 3.12.10 and FFmpeg 9.0.2, synthetic Steam folders and generated media. Initial run: 39 tests, 34 passed and five Linux/symlink tests skipped. Bash syntax checked with Git Bash 5.3.9. Windows runs the embedded converter directly and is not a supported deployment target.

Linux CI results will be recorded before this release is published.

Not tested on Joe's real Steam Machine, Steam Deck, SteamOS Gaming Mode, or TV HDMI boot/wake path. There is no claim that the delay resolves a specific display's clipping, that every Steam build accepts the output, or that Flatpak Steam playback has been verified.
