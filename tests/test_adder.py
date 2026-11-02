"""Synthetic media tests. Linux runs the shipped Bash entry point.

Windows checks the same embedded Python engine directly, not the launcher.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'startup_movie_delay_adder.sh'
ENGINE = SCRIPT.read_text(encoding='utf-8').split("<<'SMDA_PYTHON'\n", 1)[1].rsplit('\nSMDA_PYTHON', 1)[0]
M = {'__name__': 'smda'}
exec(compile(ENGINE, str(SCRIPT), 'exec'), M)
OPAQUE = '40135155972_4c1529579cf8441986ed3e092c436213adc02920.webm'
ENTRY = [sys.executable, '-c', ENGINE] if os.name == 'nt' else ['bash', str(SCRIPT)]


def cmd(*args, env=None, input=None, check=True, binary=False):
    p = subprocess.run(list(map(str, args)), input=input, env=env, capture_output=True,
                       text=not binary, encoding=None if binary else 'utf-8', timeout=120)
    if check and p.returncode:
        raise AssertionError(f'{args[0]} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}')
    return p


def media(path, audio=True, seconds=1.2, rate='30'):
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', f'testsrc2=s=160x90:r={rate}:d={seconds}']
    if audio:
        args += ['-f', 'lavfi', '-i', f'sine=f=440:r=48000:d={seconds}']
    args += ['-c:v', 'libvpx', '-b:v', '350k']
    if audio:
        args += ['-c:a', 'libvorbis', '-shortest']
    cmd(*args, path)


def probe(path):
    return json.loads(cmd('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path).stdout)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SafetyTests(unittest.TestCase):
    def test_title_sanitization_unicode_and_length(self):
        clean = M['clean_title']
        self.assertEqual(clean(' café   秋 🍂 '), 'café 秋 🍂')
        self.assertNotIn('\u202e', clean('abc\u202ewebm'))
        self.assertNotIn('\x1b', clean('abc\x1b[31m'))
        self.assertLessEqual(len(clean('秋' * 200).encode()), 160)
        self.assertEqual(clean(' ... / \\ '), '')
        self.assertNotIn('/', clean('../../outside'))

    def test_numeric_titles_not_guessed(self):
        for name in ('123.webm', OPAQUE, 'a' * 32 + '.webm', '123_abcdabcdabcdabcd.mp4'):
            self.assertEqual(M['filename_title'](Path(name)), '')
        self.assertEqual(M['filename_title'](Path('cozy_fall.webm')), 'cozy fall')
        self.assertEqual(M['filename_title'](Path('oled_startup.webm')), 'Steam Deck OLED Startup')

    def test_unsupported_media_and_invalid_numbers(self):
        good = {'format': {'duration': '1.2'}, 'streams': [
            {'codec_type': 'video', 'width': 160, 'height': 90, 'avg_frame_rate': '30/1'}]}
        changes = [('format', 'duration', 'nan'), ('format', 'duration', '-1'), ('format', 'duration', '0'),
                   ('format', 'duration', '121'), ('video', 'avg_frame_rate', '0/0'),
                   ('video', 'avg_frame_rate', '999/1'), ('video', 'width', 99999),
                   ('video', 'color_transfer', 'smpte2084'), ('video', 'color_transfer', 'arib-std-b67'),
                   ('video', 'pix_fmt', 'yuva420p'), ('video', 'tags', {'alpha_mode': '1'}),
                   ('video', 'start_time', '5'), ('video', 'side_data_list', [{'rotation': 90}])]
        for location, key, value in changes:
            with self.subTest(key=key, value=value):
                data = json.loads(json.dumps(good))
                target = data['format'] if location == 'format' else data['streams'][0]
                target[key] = value
                with self.assertRaises(M['Problem']):
                    M['media_info'](data)

    def test_missing_ffmpeg_features(self):
        with patch.dict(M, {'run': lambda *a, **k: ''}):
            with self.assertRaisesRegex(M['Problem'], 'libvpx'):
                M['capabilities']('ffmpeg', False)
        listing = ' V..... libvpx\n T.. fps\n T.. setpts\n T.. tpad\n T.. pad\n T.. format\n E webm\n'
        with patch.dict(M, {'run': lambda *a, **k: listing}):
            M['capabilities']('ffmpeg', False)
            with self.assertRaisesRegex(M['Problem'], 'libvorbis'):
                M['capabilities']('ffmpeg', True)

    def test_missing_ffprobe(self):
        with patch.object(M['shutil'], 'which', side_effect=lambda n: '/ffmpeg' if n == 'ffmpeg' else None):
            with self.assertRaisesRegex(M['Problem'], 'ffprobe is missing'):
                M['find_tools']()

    def test_timeout_and_missing_executable(self):
        with self.assertRaisesRegex(M['Problem'], 'timed out'):
            M['run']([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.05)
        with self.assertRaisesRegex(M['Problem'], 'Could not run'):
            M['run'](['no-such-smda-executable-8547'])

    def test_invalid_probe_response(self):
        for result in ('bad json', '[]', '{}', '{"streams": [], "format": []}'):
            with patch.dict(M, {'run': lambda *a, **k: result}):
                with self.assertRaisesRegex(M['Problem'], 'invalid media'):
                    M['probe']('ffprobe', Path('x.webm'))


class MovieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('ffmpeg', 'ffprobe'):
            if not shutil.which(tool):
                raise RuntimeError(f'{tool} is required; integration tests must not silently skip.')
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.fixtures.name) / 'sample.webm'
        cls.silent = Path(cls.fixtures.name) / 'silent.webm'
        media(cls.sample)
        media(cls.silent, audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='smda tests ')
        self.home = Path(self.tmp.name)
        self.steam = self.home / 'Steam'
        self.points = self.steam / 'config/communityitemscache/startupmovies'
        self.custom = self.steam / 'config/uioverrides/movies'
        self.built = self.steam / 'steamui/movies'
        self.points.mkdir(parents=True)
        self.built.mkdir(parents=True)
        self.env = {**os.environ, 'HOME': str(self.home), 'SMDA_STEAM_ROOT': str(self.steam),
                    'XDG_CONFIG_HOME': str(self.home / 'cfg'), 'PYTHONUTF8': '1'}
        self.src = self.points / OPAQUE
        shutil.copyfile(self.sample, self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, *args, input=None, check=True):
        return cmd(*ENTRY, *args, input=input, env=self.env, check=check)

    def make_copy(self, source=None, title='Test', delay='1', check=True):
        return self.script('--source', source or self.src, '--title', title, '--delay', delay, '--yes', check=check)

    def output(self, title='Test', delay=1):
        return self.custom / f'{title} - {delay}s Delay.webm'

    def no_temps(self):
        self.assertEqual(list(self.custom.glob('.smda-*')), [])

    def link(self, source, target, directory=False):
        try:
            target.symlink_to(source, target_is_directory=directory)
        except OSError as exc:
            self.skipTest(f'OS cannot create test symlinks: {exc}')

    def test_help_version_without_steam(self):
        self.env['SMDA_STEAM_ROOT'] = str(self.home / 'missing')
        self.assertIn('--source', self.script('--help').stdout)
        self.assertIn('1.0.0-rc.1', self.script('--version').stdout)

    def test_discovery_relevant_files_no_writes(self):
        for name in ('startup_machine.webm', 'steam_controller_thumbstick.webm',
                     'deck-suspend-animation.webm', 'unrelated_startup_something.webm'):
            (self.built / name).touch()
        (self.points / 'Test - 6s Delay.webm').touch()
        nested = self.points / 'one/two'
        nested.mkdir(parents=True)
        (nested.parent / 'found.MP4').touch()
        (nested / 'too_deep.webm').touch()
        before = sorted(p.relative_to(self.home) for p in self.home.rglob('*'))
        text = self.script('--list').stdout
        for included in ('Steam Machine Startup', 'Unknown title', 'found.MP4'):
            self.assertIn(included, text)
        for excluded in ('thumbstick', 'suspend', 'unrelated', '6s Delay', 'too_deep'):
            self.assertNotIn(excluded, text)
        self.assertEqual(before, sorted(p.relative_to(self.home) for p in self.home.rglob('*')))

    def test_steam_aliases_and_file_symlinks_deduplicated(self):
        self.env.pop('SMDA_STEAM_ROOT')
        (self.home / '.steam').mkdir()
        self.link(self.steam, self.home / '.steam/root', True)
        self.link(self.steam, self.home / '.steam/steam', True)
        self.link(self.src, self.points / 'alias.webm')
        self.assertEqual(self.script('--list').stdout.count('[Points Shop]'), 1)

    def test_flatpak_and_ambiguous_installation(self):
        self.env.pop('SMDA_STEAM_ROOT')
        (self.home / '.var/app/com.valvesoftware.Steam/.local/share/Steam/config').mkdir(parents=True)
        self.assertIn('No movies found', self.script('--list').stdout)
        (self.home / '.local/share/Steam/config').mkdir(parents=True)
        self.assertIn('More than one Steam', self.script('--list', check=False).stderr)

    def test_bad_steam_and_relative_config(self):
        self.env['SMDA_STEAM_ROOT'] = str(self.home / 'missing')
        self.assertNotEqual(self.script('--list', check=False).returncode, 0)
        self.env['SMDA_STEAM_ROOT'] = str(self.steam)
        self.env['XDG_CONFIG_HOME'] = 'relative'
        self.assertIn('absolute', self.script('--list', check=False).stderr)

    def test_cli_conflicts_invalid_values_and_eof(self):
        cases = [('--yes',), ('--source',), ('--wat',), ('--list', '--source', str(self.src)),
                 ('--source', str(self.src), '--list'), ('--list', '--delay', '1')]
        for args in cases:
            self.assertNotEqual(self.script(*args, check=False).returncode, 0)
        for delay in ('0', '21', '-1', '1.5', 'NaN', '9' * 100):
            self.assertNotEqual(self.make_copy(delay=delay, check=False).returncode, 0)
        self.assertIn('No input received', self.script(input='', check=False).stderr)
        self.assertFalse(self.custom.exists())

    def test_cancel_confirmation_defaults_no(self):
        self.assertIn('Cancelled', self.script(input='q\n').stdout)
        self.assertIn('Cancelled', self.script('--source', self.src, '--title', 'Test', '--delay', '1', input='\n').stdout)
        self.assertFalse(self.custom.exists())

    def test_interactive_create_remember_original_unchanged(self):
        before = sha(self.src)
        p = self.script(input='1\nSpecial Delivery\n\ny\n')
        self.assertIn('Saved:', p.stdout)
        self.assertEqual(sha(self.src), before)
        self.assertAlmostEqual(float(probe(self.output('Special Delivery', 6))['format']['duration']), 7.2, delta=.15)
        self.assertIn('Special Delivery', self.script('--list').stdout)
        self.script('--source', self.src, '--delay', '2', '--yes')
        self.assertTrue(self.output('Special Delivery', 2).exists())
        self.no_temps()

    def test_yes_default_delay_never_prompts(self):
        self.script('--source', self.src, '--title', 'Default', '--yes', input='')
        self.assertTrue(self.output('Default', 6).exists())

    def test_unknown_numeric_requires_title(self):
        for name in (OPAQUE, '123.webm'):
            source = self.points / name
            if source != self.src:
                shutil.copyfile(self.sample, source)
            self.assertIn('--title', self.script('--source', source, '--yes', input='', check=False).stderr)
        self.assertFalse(self.custom.exists())

    def test_embedded_title_before_filename(self):
        source = self.home / 'filename.webm'
        cmd('ffmpeg', '-v', 'error', '-i', self.src, '-c', 'copy', '-metadata', 'title=Embedded Movie', source)
        self.script('--source', source, '--delay', '1', '--yes')
        self.assertTrue(self.output('Embedded Movie').exists())

    def test_cache_key_is_content_not_basename(self):
        self.make_copy(title='First')
        other = self.home / OPAQUE
        shutil.copyfile(self.silent, other)
        self.assertIn('Unknown video title', self.script('--source', other, '--yes', check=False).stderr)

    def test_bad_cache_and_legacy_tsv_not_executed(self):
        cache = self.home / 'cfg/startup-movie-delay-adder/titles'
        cache.mkdir(parents=True)
        entry = cache / (sha(self.src) + '.json')
        for data in ('not json', '[]', '{"title": 33}', 'x' * 5000):
            entry.write_text(data)
            self.assertIn('Unknown title', self.script('--list').stdout)
        (cache.parent / 'movie-titles.tsv').write_text('$(touch nope)\tOther\n')
        self.assertFalse((self.home / 'nope').exists())

    def test_cache_symlink_never_written(self):
        cache = self.home / 'cfg/startup-movie-delay-adder/titles'
        cache.mkdir(parents=True)
        victim = self.home / 'victim.json'
        victim.write_text('original')
        self.link(victim, cache / (sha(self.src) + '.json'))
        self.make_copy()
        self.assertEqual(victim.read_text(), 'original')

    def test_no_overwrite_file_directory_dangling_symlink(self):
        self.custom.mkdir(parents=True)
        dest = self.output()
        dest.write_bytes(b'keep me')
        self.assertIn('Already exists', self.make_copy(check=False).stderr)
        self.assertEqual(dest.read_bytes(), b'keep me')
        dest.unlink()
        dest.mkdir()
        self.assertNotEqual(self.make_copy(check=False).returncode, 0)
        self.assertEqual(list(dest.iterdir()), [])
        dest.rmdir()
        self.link(self.home / 'missing', dest)
        self.assertNotEqual(self.make_copy(check=False).returncode, 0)
        self.assertTrue(dest.is_symlink())

    def test_directory_race_cannot_receive_output(self):
        info = M['media_info'](probe(self.src))
        link = os.link
        def race(source, dest):
            Path(dest).mkdir()
            link(source, dest)
        with patch.object(M['os'], 'link', side_effect=race):
            with self.assertRaisesRegex(M['Problem'], 'without overwriting'):
                M['convert']('ffmpeg', 'ffprobe', self.src, self.output(), *info, 1, sha(self.src))
        self.assertEqual(list(self.output().iterdir()), [])
        self.no_temps()

    def test_source_change_not_installed(self):
        info = M['media_info'](probe(self.src))
        with patch.dict(M, {'fingerprint': lambda p: 'changed'}):
            with self.assertRaisesRegex(M['Problem'], 'source changed'):
                M['convert']('ffmpeg', 'ffprobe', self.src, self.output(), *info, 1, sha(self.src))
        self.assertFalse(self.output().exists())
        self.no_temps()

    def test_output_symlink_directory_rejected(self):
        outside = self.home / 'outside'
        outside.mkdir()
        self.custom.parent.mkdir(parents=True)
        self.link(outside, self.custom, True)
        self.assertIn('symlink', self.make_copy(check=False).stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_concurrent_runs_install_one_copy(self):
        args = [*ENTRY, '--source', str(self.src), '--title', 'Race', '--delay', '1', '--yes']
        jobs = [subprocess.Popen(args, env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for job in jobs:
            job.communicate(timeout=120)
        self.assertEqual(sorted(job.returncode for job in jobs), [0, 1])
        self.assertEqual(probe(self.output('Race'))['streams'][0]['codec_name'], 'vp8')
        self.no_temps()

    def test_silent_movie(self):
        self.make_copy(source=self.silent)
        data = probe(self.output())
        self.assertEqual([s['codec_type'] for s in data['streams']], ['video'])
        self.assertAlmostEqual(float(data['format']['duration']), 2.2, delta=.15)

    def test_black_frames_and_audio_timing(self):
        self.make_copy(delay='3')
        dest = self.output(delay=3)
        def frame(at):
            return cmd('ffmpeg', '-v', 'error', '-ss', at, '-i', dest, '-frames:v', '1',
                       '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1', binary=True).stdout
        for at in (0, .5, 2.8):
            pixels = frame(at)
            self.assertEqual(len(pixels), 160 * 90 * 3)
            self.assertLess(max(pixels), 3)
        pixels = frame(3.2)
        self.assertGreater(sum(pixels) / len(pixels), 20)
        def rms(at):
            raw = cmd('ffmpeg', '-v', 'error', '-ss', at, '-i', dest, '-t', '0.12', '-vn',
                      '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1', binary=True).stdout
            samples = struct.unpack('<' + 'h' * (len(raw) // 2), raw)
            self.assertTrue(samples)
            return math.sqrt(sum(x*x for x in samples) / len(samples))
        self.assertLess(rms(.5), 2)
        self.assertLess(rms(2.8), 2)
        self.assertGreater(rms(3.2), 100)

    def test_fractional_frame_rate(self):
        source = self.home / 'fractional.webm'
        media(source, audio=False, rate='30000/1001')
        self.make_copy(source=source)
        self.assertEqual(probe(self.output())['streams'][0]['avg_frame_rate'], '30000/1001')

    def test_mp4_spaces_and_60fps(self):
        source = self.home / 'Bright Autumn.mp4'
        cmd('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=128x72:r=60:d=0.7',
            '-f', 'lavfi', '-i', 'sine=f=440:r=48000:d=0.7', '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', source)
        self.script('--source', source, '--delay', '2', '--yes')
        data = probe(self.output('Bright Autumn', 2))
        self.assertAlmostEqual(float(data['format']['duration']), 2.7, delta=.15)
        self.assertEqual(data['streams'][0]['avg_frame_rate'], '60/1')

    def test_short_audio_does_not_cut_video(self):
        source = self.home / 'short-audio.webm'
        cmd('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=2',
            '-f', 'lavfi', '-i', 'sine=d=0.5:r=48000', '-c:v', 'libvpx', '-c:a', 'libvorbis', source)
        self.make_copy(source=source)
        self.assertAlmostEqual(float(probe(self.output())['format']['duration']), 3, delta=.15)

    def test_multichannel_and_subtitles(self):
        source = self.home / 'subtitles.webm'
        subtitle = self.home / 'captions.vtt'
        subtitle.write_text('WEBVTT\n\n00:00:00.100 --> 00:00:00.800\nHello\n')
        cmd('ffmpeg', '-v', 'error', '-i', self.src, '-i', subtitle, '-map', '0:v', '-map', '0:a',
            '-map', '1:0', '-c:v', 'copy', '-c:a', 'libvorbis', '-ac', '6', '-c:s', 'webvtt', source)
        result = self.make_copy(source=source)
        self.assertIn('subtitle tracks are omitted', result.stdout)
        self.assertIn('mixed down to stereo', result.stdout)
        streams = probe(self.output())['streams']
        self.assertEqual([s['codec_type'] for s in streams], ['video', 'audio'])
        self.assertEqual(streams[1]['channels'], 2)

    def test_metadata_removed_and_renamed_output_not_stacked(self):
        source = self.home / 'metadata.webm'
        cmd('ffmpeg', '-v', 'error', '-i', self.src, '-c', 'copy', '-metadata', 'comment=private-source-metadata', source)
        self.make_copy(source=source)
        self.assertNotIn('private-source-metadata', json.dumps(probe(self.output())))
        renamed = self.home / 'renamed.webm'
        shutil.copyfile(self.output(), renamed)
        self.assertIn('stacking', self.make_copy(source=renamed, check=False).stderr)

    def test_malformed_audio_only_and_renamed_playlists_rejected(self):
        for content in (b'not video', b'#EXTM3U\nhttp://127.0.0.1:9/secret\n', b'ffconcat version 1.0\nfile /etc/passwd\n'):
            bad = self.home / 'bad.mp4'
            bad.write_bytes(content)
            self.assertNotEqual(self.make_copy(source=bad, check=False).returncode, 0)
            self.assertFalse(self.custom.exists())
        audio = self.home / 'audio.webm'
        cmd('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=d=0.5', '-c:a', 'libvorbis', audio)
        self.assertIn('no readable video', self.make_copy(source=audio, check=False).stderr)

    def test_offset_timestamps_rejected(self):
        source = self.home / 'offset.webm'
        cmd('ffmpeg', '-v', 'error', '-i', self.src, '-c', 'copy', '-output_ts_offset', '5', source)
        self.assertIn('offset timestamps', self.make_copy(source=source, check=False).stderr)

    def test_title_quoting_and_unicode(self):
        title = '秋 café $(touch PWNED); `echo bad` ../demo'
        self.make_copy(title=title)
        expected = M['clean_title'](title)
        self.assertTrue(self.output(expected).is_file())
        self.assertFalse((self.home / 'PWNED').exists())
        self.assertEqual(list(self.custom.glob('*.webm')), [self.output(expected)])

    @unittest.skipIf(os.name == 'nt', 'Linux filenames')
    def test_newline_quotes_leading_dash_source(self):
        source = self.home / "-file ' $(touch PWNED)\n.webm"
        shutil.copyfile(self.sample, source)
        self.make_copy(source=source)
        self.assertTrue(self.output().exists())
        self.assertFalse((self.home / 'PWNED').exists())

    def test_encode_failure_cleans_temps(self):
        info = M['media_info'](probe(self.src))
        def failure(*a, **k):
            raise M['Problem']('simulated disk full')
        with patch.dict(M, {'run': failure}):
            with self.assertRaisesRegex(M['Problem'], 'disk full'):
                M['convert']('ffmpeg', 'ffprobe', self.src, self.output(), *info, 1, sha(self.src))
        self.assertFalse(self.output().exists())
        self.no_temps()

    def test_validation_failure_cleans_temps(self):
        info = M['media_info'](probe(self.src))
        bad = {'format': {'duration': '99'}, 'streams': []}
        with patch.dict(M, {'probe': lambda *a: bad}):
            with self.assertRaisesRegex(M['Problem'], 'validation failed'):
                M['convert']('ffmpeg', 'ffprobe', self.src, self.output(), *info, 1, sha(self.src))
        self.assertFalse(self.output().exists())
        self.no_temps()

    def test_multiple_audio_tracks_choose_first(self):
        source = self.home / 'two-audio.webm'
        cmd('ffmpeg', '-v', 'error', '-i', self.src, '-map', '0:v', '-map', '0:a', '-map', '0:a', '-c', 'copy', source)
        result = self.make_copy(source=source)
        self.assertIn('first audio track only', result.stdout)
        self.assertEqual(len([s for s in probe(self.output())['streams'] if s['codec_type'] == 'audio']), 1)

    def test_odd_dimensions_padded(self):
        source = self.home / 'odd.webm'
        cmd('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=s=161x91:r=30:d=0.6',
            '-pix_fmt', 'yuv420p', '-c:v', 'libvpx', source)
        self.make_copy(source=source)
        stream = probe(self.output())['streams'][0]
        self.assertEqual((stream['width'], stream['height']), (162, 92))

    @unittest.skipIf(os.name == 'nt', 'Linux launcher')
    def test_launcher_missing_python_is_clear(self):
        empty = self.home / 'empty-path'
        empty.mkdir()
        env = {**self.env, 'PATH': str(empty)}
        result = cmd('/bin/bash', SCRIPT, '--help', env=env, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.9', result.stderr)

    @unittest.skipIf(os.name == 'nt', 'POSIX signals and permissions')
    def test_termination_cleans_incomplete_movie(self):
        source = self.home / 'long.webm'
        cmd('ffmpeg', '-v', 'error', '-stream_loop', '99', '-i', self.src, '-t', '100', '-c', 'copy', source)
        args = [*ENTRY, '--source', str(source), '--title', 'Interrupted', '--delay', '20', '--yes']
        process = subprocess.Popen(args, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not list(self.custom.glob('.smda-*')) and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIsNone(process.poll(), 'Conversion finished before signal test could interrupt it')
            process.terminate()
            process.communicate(timeout=30)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(self.output('Interrupted', 20).exists())
            self.no_temps()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    @unittest.skipIf(os.name == 'nt', 'POSIX file permissions')
    def test_unwritable_output_fails_without_touching_source(self):
        if os.geteuid() == 0:
            self.skipTest('Run as ordinary user to test write permission denial')
        self.custom.mkdir(parents=True)
        self.custom.chmod(0o500)
        before = sha(self.src)
        try:
            result = self.make_copy(check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(sha(self.src), before)
        finally:
            self.custom.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
