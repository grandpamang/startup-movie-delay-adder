# Startup Movie Delay Adder

My TV was missing the beginning of my Steam Machine's startup movie. This is a small workaround to try: add a few seconds of black and silence before the movie starts.

The script makes a **separate copy** and leaves the original alone. You choose that copy in Steam's normal Customization menu. It doesn't change Steam settings, patch SteamOS, or install anything.

**Release candidate:** the converter has automated tests; whether it helps with a particular TV's boot or wake delay still needs testing. This release has **not been tested on my real Steam Machine or a Steam Deck**. It doesn't fix HDMI negotiation.

## Run it

In Desktop Mode, download [startup_movie_delay_adder.sh](https://github.com/grandpamang/startup-movie-delay-adder/releases/download/v1.0.0-rc.1/startup_movie_delay_adder.sh) into Downloads. Open Konsole and run:

```bash
bash ~/Downloads/startup_movie_delay_adder.sh
```

1. Pick a movie. Press **P** to preview an unfamiliar one.
2. Give it a name if Steam only supplied a numeric or hashed filename.
3. Choose a delay, from **1–20 seconds**. Enter uses **6 seconds**.
4. Check the destination and type **y** to create the copy.
5. Return to Gaming Mode → **Settings → Customization**, select the new movie, and turn off Shuffle while testing.

For example: `Special Delivery - 6s Delay.webm`. Try cold boot and sleep/wake separately. If Steam hasn't picked up the file, restart Steam and check the Customization menu again.

**Requirements:** Bash, Python **3.9+**, `ffmpeg`, and `ffprobe` available in PATH. FFmpeg needs the `libvpx` encoder and WebM muxer; movies with sound also need `libvorbis`. The script checks the filters it uses before encoding. Previewing is optional and uses `xdg-open` or `gio` plus your installed video player.

If a dependency is missing, the script stops with its name. It does not install packages or ask for sudo. SteamOS installations differ: check `python3 --version`, `ffmpeg -version`, and `ffprobe -version` in Konsole. An FFmpeg copy inside a Flatpak application isn't necessarily available to this script. Use a trusted installation appropriate to your device; [FFmpeg's download page](https://ffmpeg.org/download.html) is a starting point. Don't disable SteamOS's read-only system just to run this tool.

## Finding and naming movies

The picker checks these folders in the selected Steam installation:

- `config/communityitemscache/startupmovies` — downloaded Points Shop movies.
- `steamui/movies` — recognized built-in startup filenames, excluding controller tutorials and suspend animations.
- `config/uioverrides/movies` — custom movies.

It also checks `~/Downloads` and `~/Videos`, including one level of subfolders. The list shows each file's path. It skips hidden folders and recognizable delayed-copy filenames. A renamed generated copy is rejected when selected, using its embedded marker; removing both the marker and delayed filename defeats that check.

Common native Steam paths and the usual Flatpak data path are detected. Symlink aliases of the same installation count once. If there are multiple installations, choose explicitly:

```bash
SMDA_STEAM_ROOT="$HOME/.local/share/Steam" bash ~/Downloads/startup_movie_delay_adder.sh
```

`SMDA_STEAM_ROOT` must contain `config/`. Output goes to its `config/uioverrides/movies/` folder. Redirected `config`, `uioverrides`, or `movies` folders are rejected; ordinary `~/.steam/root` installation aliases are supported. Flatpak path discovery is tested with fixtures, not a running Flatpak Steam client.

Names come from your `--title`, a saved name, embedded title metadata, then a descriptive filename. **There is no verified universal Points Shop ID-to-title lookup here.** Unknown numeric/hash filenames need a name once. Metadata and descriptive filenames are suggestions, not proof of a Points Shop title; use `--title` to correct them. Embedded metadata is read when selecting a movie, not while listing it.

Saved names use a hash of the video contents, so two different videos with the same filename don't share a name. They live in `~/.config/startup-movie-delay-adder/titles/` (or `$XDG_CONFIG_HOME`). `--title` overrides a saved name for that run. Delete the matching JSON entry to forget it. Earlier `movie-titles.tsv` files are left untouched and aren't imported; identify those movies once again.

## Optional commands

```bash
bash startup_movie_delay_adder.sh --list
bash startup_movie_delay_adder.sh --source "$HOME/Videos/my_movie.webm" --delay 8 --yes
bash startup_movie_delay_adder.sh --source "$HOME/Videos/123.webm" --title 'Special Delivery' --yes
bash startup_movie_delay_adder.sh --help
```

`--list` writes nothing and doesn't require FFmpeg. `--yes` requires an explicit source and never prompts; omitted delay means six seconds. An unknown title still needs `--title`. Existing output names cause an error: choose another title/delay or use the copy you already made.

## FAQ and limitations

**Does this work with every video?** No. Input must be a local WebM or MP4, up to 120 seconds, with a usable 1–120 fps video stream. HDR, transparency, rotation metadata, and start timestamps more than 0.1 seconds from zero are rejected. Export those as an ordinary SDR clip with audio/video starting at zero first.

**Does it preserve quality?** The copy is re-encoded as VP8/Vorbis WebM. Dimensions are kept, except odd dimensions gain one black edge pixel. Constant frame rates, including fractional rates, are retained; variable frame rate is converted to the reported average rate. Timing is rounded to video frames and audio samples. This is not a lossless edit.

**What happens to sound and subtitles?** It uses the first actual video stream and first audio track. Mono/stereo sound is delayed with the picture and resampled to 48 kHz; more channels are mixed down to stereo. Silent videos remain silent. Separate subtitle tracks, attachments, chapters, and source metadata are omitted. Burned-in subtitles stay in the picture. Short audio does not truncate the video. Extra tracks and subtitle removal are reported before confirmation.

**Is there a length limit in Steam?** This script warns above 30 seconds; that is a caution, not a verified universal Steam limit. Valve's [Points Shop submission guidelines](https://partner.steamgames.com/doc/marketing/pointsshopitems) apply to submissions and don't establish the limit for local custom movies.

**Can it damage the original or overwrite a copy?** It reads the source and encodes into a private temporary folder. It checks the resulting streams and duration, fully decodes the result, and checks the source hash again before installing the copy. The final save uses a hard link that fails if any file, directory, or symlink already has that name, including during concurrent runs. The destination filesystem must support hard links. Interrupted or failed conversions clean up their temporary folder; a power loss or forced kill can leave a hidden `.smda-*` folder to remove later. Nothing here protects against another program deliberately modifying your files at the same time.

**How do I undo it?** Select your original movie in Steam, then delete just the generated `… - 6s Delay.webm` copy. No installer or Steam setting changes need reversing.

The converter makes no network requests. It restricts FFmpeg to local MP4/WebM demuxing and doesn't execute filenames or metadata as commands. Previewing opens your local player, whose behavior is separate. Use media you trust and keep FFmpeg updated. No startup videos are bundled, and this code's license doesn't grant rights to redistribute someone else's movies.

## Tests and contributions

On Linux with Python, FFmpeg, and ShellCheck installed:

```bash
bash -n startup_movie_delay_adder.sh
shellcheck startup_movie_delay_adder.sh
python3 -m unittest discover -s tests -v
```

The suite generates its own media. It checks black frames, audio silence and onset, durations, frame rates, subtitles, filename handling, discovery, caching, collisions, and failure cleanup. GitHub Actions runs the actual Bash launcher on Ubuntu 22.04 and 24.04 with read-only repository permissions and a pinned checkout action. [Release notes](RELEASE_NOTES.md) separate these checks from real-device testing.

If you try it, include your SteamOS/Steam version, device, delay, and whether it worked on boot, wake, or both. Share the error text if it failed. Please leave out account details, full Steam configs, and copyrighted videos.

MIT license; the original project's [license notice](LICENSE) is retained. Independent community utility, not affiliated with Valve. Valve documents the [Customization picker here](https://www.steamdeck.com/en/news?p=121).
