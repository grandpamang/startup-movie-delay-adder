#!/usr/bin/env bash
# Startup Movie Delay Adder. Copyright (c) 2026 Contributors. MIT License.
# One download; Python handles paths and metadata without shell interpolation.
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'ERROR: Python 3.9 or newer is required. See the README; nothing was changed.' >&2
  exit 1
fi
exec python3 -c "$(cat <<'SMDA_PYTHON'
import sys
if sys.version_info < (3, 9):
    sys.exit('ERROR: Python 3.9 or newer is required.')

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import unicodedata
from fractions import Fraction

VERSION = '1.0.0-rc.1'
GENERATED = re.compile(r'( - \d+s Delay|_delayed_\d+s|_\d+s_Delay)\.webm$', re.I)
OPAQUE = re.compile(r'(?:\d+|[0-9a-f]{16,}|\d+_[0-9a-f]{16,})$', re.I)
BUILTINS = {
    'startup_machine': 'Steam Machine Startup',
    'deck_startup': 'Steam Deck Startup',
    'oled_startup': 'Steam Deck OLED Startup',
    'bigpicture_startup': 'Steam Big Picture Startup',
    'steam_os_family_startup': 'Steam OS Family Startup',
    'steam_os_startup': 'Steam OS Startup',
    'steamframe_startup_450x450': 'Steam Frame Startup',
}
# Restrict input demuxers as well as protocols: a playlist renamed .mp4 must
# never make FFmpeg read other local files or contact a server.
INPUT_OPTIONS = ['-protocol_whitelist', 'file', '-format_whitelist', 'matroska,webm,mov']


class Problem(Exception):
    pass


def visible(value):
    return ''.join(c if not unicodedata.category(c).startswith('C') else ' ' for c in str(value))


def say(value=''):
    print(visible(value), flush=True)


def clean_title(value):
    value = unicodedata.normalize('NFC', visible(value))
    value = re.sub(r'[/\\:<>|?*\"]', ' ', value)
    value = ' '.join(value.split()).strip(' ._-')
    # Limit bytes, without splitting UTF-8 characters, for Linux NAME_MAX.
    return value.encode('utf-8')[:160].decode('utf-8', errors='ignore').rstrip(' ._-')


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        raise Problem('No input received. For automatic use, pass --source FILE --yes.') from None


def run(args, timeout=30):
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Problem(f'{Path(args[0]).name} timed out; no completed movie was installed.') from None
    except OSError as exc:
        raise Problem(f'Could not run {Path(args[0]).name}: {exc}') from None
    if p.returncode:
        detail = visible(p.stderr.strip())[-1600:]
        raise Problem(f'{Path(args[0]).name} failed. {detail}')
    return p.stdout


def home_dir():
    return Path(os.environ.get('HOME') or str(Path.home())).expanduser().resolve()


def steam_dir(home):
    override = os.environ.get('SMDA_STEAM_ROOT')
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / 'config').is_dir():
            raise Problem('SMDA_STEAM_ROOT must point to a Steam directory containing config/.')
        return root
    roots = []
    for candidate in (home / '.steam/root', home / '.steam/steam', home / '.local/share/Steam',
                      home / '.var/app/com.valvesoftware.Steam/.local/share/Steam'):
        root = candidate.resolve()
        if (root / 'config').is_dir() and root not in roots:
            roots.append(root)
    if not roots:
        raise Problem('Steam was not found. Run in Desktop Mode, or set SMDA_STEAM_ROOT to your Steam folder.')
    if len(roots) != 1:
        raise Problem('More than one Steam installation found. Set SMDA_STEAM_ROOT to the one you use.')
    return roots[0]


def cache_dir(home):
    base = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    if not base.is_absolute():
        raise Problem('XDG_CONFIG_HOME must be an absolute path.')
    return base / 'startup-movie-delay-adder' / 'titles'


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_title(cache, digest):
    file = cache / (digest + '.json')
    # Do not follow a title-cache symlink. Existing TSV files are left alone.
    if cache.is_symlink() or file.is_symlink():
        return ''
    try:
        if file.stat().st_size > 4096:
            return ''
        value = json.loads(file.read_text(encoding='utf-8'))
        return clean_title(value['title']) if isinstance(value.get('title'), str) else ''
    except (OSError, ValueError, AttributeError, TypeError):
        return ''


def remember_title(cache, digest, title):
    # Optional, one file per video content hash; exclusive creation avoids
    # replacing either an existing title or a symlink at its destination.
    try:
        if cache.is_symlink() or cache.parent.is_symlink():
            raise OSError('title-cache directory is a symlink')
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = cache / (digest + '.json')
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'title': title}, stream, ensure_ascii=False)
            stream.write('\n')
    except FileExistsError:
        pass
    except OSError as exc:
        say(f'Note: Movie saved, but its name could not be remembered: {exc}')


def filename_title(path):
    if OPAQUE.fullmatch(path.stem):
        return ''
    return BUILTINS.get(path.stem.lower(), clean_title(path.stem.replace('_', ' ').replace('-', ' ')))


def discover(root, home):
    folders = [(root / 'config/communityitemscache/startupmovies', 'Points Shop', False),
               (root / 'config/uioverrides/movies', 'Custom', False),
               (root / 'steamui/movies', 'Built-in', True),
               (home / 'Downloads', 'Downloads', False), (home / 'Videos', 'Videos', False)]
    found, seen = [], set()
    for folder, category, builtin in folders:
        if not folder.is_dir():
            continue
        def warning(exc):
            say(f'Note: Could not scan {exc.filename}: {exc.strerror}')
        for directory, dirs, files in os.walk(folder, followlinks=False, onerror=warning):
            # Match the old picker: files here and one directory below it.
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(directory) / d).is_symlink())
            if Path(directory) != folder:
                dirs[:] = []
            for name in sorted(files, key=str.casefold):
                path = Path(directory) / name
                if path.suffix.lower() not in ('.webm', '.mp4') or GENERATED.search(name) or name.startswith('.'):
                    continue
                if builtin and path.stem.lower() not in BUILTINS:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    identity = resolved.stat()
                    key = (identity.st_dev, identity.st_ino)
                    if not stat.S_ISREG(identity.st_mode) or key in seen:
                        continue
                    if GENERATED.search(resolved.name):
                        continue
                    seen.add(key)
                    found.append((resolved, category))
                except (OSError, RuntimeError):
                    continue
    return found


def label(path, cache):
    title = filename_title(path)
    if not title:
        try:
            title = read_title(cache, fingerprint(path))
        except OSError:
            pass
    return title or 'Unknown title (preview to identify)'


def show_movies(movies, cache):
    say('Startup Movie Delay Adder')
    if not movies:
        say('No movies found. Download one in Steam > Settings > Customization, or use --source FILE.')
    for i, (path, category) in enumerate(movies, 1):
        say(f'{i:2}) {label(path, cache)} [{category}]')
        say(f'    {path}')


def preview(path):
    opener = shutil.which('xdg-open') or shutil.which('gio')
    if not opener:
        say(f'Open this movie in your video player: {path}')
        return
    args = [opener] + (['open'] if Path(opener).name == 'gio' else []) + [str(path)]
    try:
        run(args, timeout=15)
    except Problem:
        say(f'Could not open a preview automatically. Open this file in your video player: {path}')


def choose(movies, cache):
    if not movies:
        raise Problem('No movies found. Use --source FILE to choose a local .webm or .mp4.')
    while True:
        show_movies(movies, cache)
        choice = ask('Movie number, P to preview, or Q to quit: ')
        if choice.lower() == 'q':
            return None
        watching = choice.lower() == 'p'
        if watching:
            choice = ask('Preview which number? ')
        if not re.fullmatch(r'[0-9]{1,5}', choice) or not 1 <= int(choice) <= len(movies):
            say('Choose a number from the list.')
            continue
        path = movies[int(choice) - 1][0]
        if not watching:
            return path
        preview(path)


def find_tools():
    found = {}
    for name in ('ffmpeg', 'ffprobe'):
        found[name] = shutil.which(name)
        if not found[name]:
            raise Problem(f'{name} is missing from PATH. See the README; no movie was written.')
    return found['ffmpeg'], found['ffprobe']


def probe(ffprobe, path):
    raw = run([ffprobe, '-v', 'error', *INPUT_OPTIONS, '-show_streams', '-show_format', '-of', 'json', str(path)])
    try:
        data = json.loads(raw)
        if not isinstance(data.get('streams'), list) or not isinstance(data.get('format'), dict):
            raise ValueError()
        return data
    except (ValueError, AttributeError):
        raise Problem('FFprobe returned invalid media information.') from None


def number(value, what):
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise Problem(f'Could not read a finite {what}.') from None


def media_info(data):
    streams, fmt = data['streams'], data['format']
    videos = [s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if not videos:
        raise Problem('Selected file has no readable video (cover art does not count).')
    v, a = videos[0], audios[0] if audios else None
    duration = number(fmt.get('duration'), 'video duration')
    if not 0 < duration <= 120:
        raise Problem('Use a short startup movie: source duration must be greater than 0 and at most 120 seconds.')
    for stream in [fmt, v] + ([a] if a else []):
        if abs(number(stream.get('start_time', 0), 'start time')) > 0.1:
            raise Problem('This file has offset timestamps. Export a copy with audio/video starting at zero first.')
    if v.get('color_transfer') in ('smpte2084', 'arib-std-b67') or 'bt2020' in v.get('color_space', ''):
        raise Problem('HDR sources are not supported. Export an SDR copy first.')
    if v.get('tags', {}).get('alpha_mode') == '1' or v.get('pix_fmt', '').startswith(('yuva', 'rgba', 'bgra', 'gbrap')):
        raise Problem('Transparent video is not supported. Export an opaque SDR copy first.')
    rotations = [s.get('rotation', 0) for s in v.get('side_data_list', [])]
    rotations.append(v.get('tags', {}).get('rotate', 0))
    if any(number(r, 'rotation') % 360 for r in rotations):
        raise Problem('Rotated video is not supported. Export with rotation baked into the picture first.')
    w, h = v.get('width', 0), v.get('height', 0)
    if not isinstance(w, int) or not isinstance(h, int) or not (1 <= w <= 8192 and 1 <= h <= 8192):
        raise Problem('Unsupported video dimensions (maximum 8192 pixels on either side).')
    try:
        rate = Fraction(v.get('avg_frame_rate', '0/1'))
        if not 1 <= rate <= 120:
            raise ValueError()
    except (ValueError, ZeroDivisionError):
        raise Problem('Could not read a supported frame rate (1 to 120 fps).') from None
    if len(videos) > 1 or len(audios) > 1:
        say('Note: Using the first video and first audio track only.')
    if any(s.get('codec_type') == 'subtitle' for s in streams):
        say('Note: Separate subtitle tracks are omitted. Subtitles already in the picture remain visible.')
    if a and a.get('channels', 0) > 2:
        say('Note: Multichannel audio will be mixed down to stereo.')
    return v, a, duration, rate


def capabilities(ffmpeg, audio):
    requirements = {'-encoders': {'libvpx'} | ({'libvorbis'} if audio else set()),
                    '-filters': {'fps', 'setpts', 'tpad', 'format', 'pad'} | ({'asetpts', 'adelay'} if audio else set()),
                    '-muxers': {'webm'}}
    for option, required in requirements.items():
        listing = run([ffmpeg, '-hide_banner', option])
        available = {line.split()[1] for line in listing.splitlines() if len(line.split()) >= 2}
        missing = required - available
        if missing:
            raise Problem(f'Your FFmpeg build lacks {", ".join(sorted(missing))} ({option[1:]}). No movie was written.')
    if audio and 'all' not in run([ffmpeg, '-hide_banner', '-h', 'filter=adelay']).split():
        raise Problem('Your FFmpeg build lacks adelay all-channel support. Update FFmpeg.')


def convert(ffmpeg, ffprobe, source, dest, v, a, duration, rate, delay, digest):
    # Reject redirected output directories; normal ~/.steam/root symlinks have
    # already been resolved when selecting the Steam installation.
    for directory in (dest.parent.parent.parent, dest.parent.parent, dest.parent):
        if directory.is_symlink():
            raise Problem(f'Output directory is a symlink: {directory}. Use a regular directory.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(dest):
        raise Problem(f'Already exists: {dest}. Nothing was overwritten; choose another title or delay.')
    v_offset = max(0, number(v.get('start_time', 0), 'video start time'))
    vf = (f'fps={rate.numerator}/{rate.denominator},setpts=PTS-STARTPTS,'
          f'tpad=start_duration={delay + v_offset:.6f}:start_mode=add:color=black,'
          'pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p')
    with tempfile.TemporaryDirectory(prefix='.smda-', dir=dest.parent) as tmp:
        temp = Path(tmp) / 'movie.webm'
        args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror', '-n',
                *INPUT_OPTIONS, '-noautorotate', '-i', str(source), '-map', f'0:{v["index"]}',
                '-vf', vf, '-c:v', 'libvpx', '-b:v', '6000k', '-crf', '16',
                '-deadline', 'good', '-cpu-used', '5', '-threads', '4']
        if a:
            audio_delay = round((delay + max(0, number(a.get('start_time', 0), 'audio start time'))) * 1000)
            args += ['-map', f'0:{a["index"]}', '-af', f'asetpts=PTS-STARTPTS,adelay={audio_delay}:all=1',
                     '-c:a', 'libvorbis', '-b:a', '160k', '-ar', '48000', '-ac', str(min(2, a.get('channels', 2)))]
        args += ['-map_metadata', '-1', '-map_chapters', '-1', '-metadata', 'SMDA_GENERATED=1', '-f', 'webm', str(temp)]
        say('Creating the copy. Large videos may take several minutes...')
        run(args, timeout=1800)
        result = probe(ffprobe, temp)
        videos = [s for s in result['streams'] if s.get('codec_type') == 'video']
        audios = [s for s in result['streams'] if s.get('codec_type') == 'audio']
        actual = number(result['format'].get('duration'), 'output duration')
        expected = duration + delay
        tolerance = max(0.15, 2 / float(rate))
        if len(videos) != 1 or videos[0].get('codec_name') != 'vp8' or abs(actual - expected) > tolerance:
            raise Problem('Output validation failed (codec or duration). No completed movie was installed.')
        if bool(a) != bool(audios) or (audios and audios[0].get('codec_name') != 'vorbis'):
            raise Problem('Output audio validation failed. No completed movie was installed.')
        run([ffmpeg, '-v', 'error', '-nostdin', '-xerror', *INPUT_OPTIONS, '-i', str(temp),
             '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'], timeout=1800)
        if fingerprint(source) != digest:
            raise Problem('The source changed during conversion. No completed movie was installed; try again.')
        # link() is atomic and fails for ANY existing destination: file,
        # directory or dangling symlink. Never fall back to an overwriting move.
        os.chmod(temp, 0o644)
        try:
            os.link(temp, dest)
        except OSError as exc:
            raise Problem(f'Could not save without overwriting: {exc}. The destination must support hard links.') from None


def main():
    parser = argparse.ArgumentParser(description='Make a separate startup movie with a black, silent lead-in.')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--list', action='store_true', help='list discovered files; write nothing')
    selection.add_argument('--source', type=Path, help='local .webm or .mp4 file')
    parser.add_argument('--title', help='title to use for the new copy')
    parser.add_argument('--delay', type=int, choices=range(1, 21), metavar='1..20', help='seconds (default: 6)')
    parser.add_argument('--yes', action='store_true', help='no prompts; requires --source')
    parser.add_argument('--version', action='version', version=VERSION)
    args = parser.parse_args()
    if args.yes and not args.source:
        parser.error('--yes requires --source')
    if args.list and (args.title is not None or args.delay is not None):
        parser.error('--list cannot be combined with --title or --delay')
    if args.title is not None and not clean_title(args.title):
        parser.error('--title contains no usable filename characters')
    home = home_dir()
    root = steam_dir(home)
    cache = cache_dir(home)
    if args.list or not args.source:
        movies = discover(root, home)
        if args.list:
            show_movies(movies, cache)
            return
        source = choose(movies, cache)
        if source is None:
            say('Cancelled. No movie was written.')
            return
    else:
        if args.source.suffix.lower() not in ('.mp4', '.webm'):
            raise Problem('Only local .webm and .mp4 files are supported.')
        if GENERATED.search(args.source.name):
            raise Problem('This looks like a delayed copy. Pick the original to avoid stacking delays.')
        source = args.source.expanduser().resolve(strict=True)
    if source.suffix.lower() not in ('.mp4', '.webm') or not source.is_file():
        raise Problem('Choose a regular local .webm or .mp4 file.')
    if GENERATED.search(source.name):
        raise Problem('This looks like a delayed copy. Pick the original to avoid stacking delays.')
    ffmpeg, ffprobe = find_tools()
    data = probe(ffprobe, source)
    tags = {k.lower(): str(value) for k, value in data['format'].get('tags', {}).items()}
    if tags.get('smda_generated') == '1':
        raise Problem('This is a generated copy, even if renamed. Pick the original to avoid stacking delays.')
    v, a, duration, rate = media_info(data)
    digest = fingerprint(source)
    title = clean_title(args.title) if args.title is not None else (
        read_title(cache, digest) or clean_title(tags.get('title', '')) or filename_title(source))
    while not title:
        if args.yes:
            raise Problem('Unknown video title. Add --title NAME, or run interactively to preview and name it.')
        response = ask('Movie title (P to preview, Q to cancel): ')
        if response.lower() == 'q':
            say('Cancelled. No movie was written.')
            return
        if response.lower() == 'p':
            preview(source)
        else:
            title = clean_title(response)
    delay = args.delay
    if delay is None:
        if args.yes:
            delay = 6
        else:
            answer = ask('Black-screen delay in seconds [6]: ') or '6'
            if not re.fullmatch(r'(?:[1-9]|1[0-9]|20)', answer):
                raise Problem('Choose a whole number from 1 to 20 seconds.')
            delay = int(answer)
    dest = root / 'config/uioverrides/movies' / f'{title} - {delay}s Delay.webm'
    if os.path.lexists(dest):
        raise Problem(f'Already exists: {dest}. Nothing was overwritten; choose another title or delay.')
    capabilities(ffmpeg, a is not None)
    say(f'Movie: {title}')
    say(f'Delay: {delay}s; total length about {duration + delay:.2f}s')
    say(f'Save to: {dest}')
    if duration + delay > 30:
        say('Note: This copy exceeds 30 seconds. Steam playback limits vary; it may be cut off.')
    if not args.yes and ask('Create this copy? [y/N]: ').lower() not in ('y', 'yes'):
        say('Cancelled. No movie was written.')
        return
    convert(ffmpeg, ffprobe, source, dest, v, a, duration, rate, delay, digest)
    if OPAQUE.fullmatch(source.stem):
        remember_title(cache, digest, title)
    say(f'Saved: {dest}')
    say('Select the new movie in Steam > Settings > Customization. Disable Shuffle to test this copy.')
    say('Test cold boot and wake separately. Your original and Steam settings were left alone.')


def interrupted(signum, frame):
    raise KeyboardInterrupt()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except KeyboardInterrupt:
        print('\nCancelled. Any incomplete conversion was discarded.', file=sys.stderr)
        sys.exit(130)
    except (Problem, OSError, RuntimeError) as exc:
        print('ERROR: ' + visible(exc), file=sys.stderr)
        sys.exit(1)
SMDA_PYTHON
)" "$@"
