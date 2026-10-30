"""Linux launcher's ISA rewrite decision and probe-argument tests."""
from paths import ROOT
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bash'), 'requires Linux bash')
class RunIsaRewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'test work'
        self.root.mkdir()
        self.out = self.root / 'out dir'
        self.out.mkdir()
        self.boot = self.root / 'boot linked.bin'
        self.boot.touch()
        self.probe = self.root / 'probe'
        self.probe.write_text(
            '#!/bin/sh\n'
            'if [ -n "$PROBE_OUTPUT" ]; then printf "%s\\n" "$PROBE_OUTPUT"; fi\n'
            'exit "${PROBE_STATUS:-0}"\n', encoding='utf-8')
        self.python = self.root / 'python'
        self.python.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$GENERATOR_LOG"\n'
            'if [ "${GENERATOR_STATUS:-0}" -eq 0 ]; then\n'
            '  while [ "$#" -gt 0 ]; do\n'
            '    if [ "$1" = "--out" ]; then shift; : > "$1/isa-rewrite.bin"; break; fi\n'
            '    shift\n'
            '  done\n'
            'fi\n'
            'if [ -n "${GENERATOR_OUTPUT:-}" ]; then printf "%s\\n" "$GENERATOR_OUTPUT"; fi\n'
            'exit "${GENERATOR_STATUS:-0}"\n', encoding='utf-8')
        self.probe.chmod(0o755)
        self.python.chmod(0o755)
        self.generator_log = self.root / 'generator calls.txt'

    def run_preflight(self, features, status=0, policy=None, generator_status=0, generator_output=''):
        env = os.environ.copy()
        env.pop('BB_ISA_REWRITE', None)
        env.update({
            'ISA_REWRITE_HELPER': str(ROOT / 'scripts' / 'isa_rewrite_preflight.sh'),
            'FAKE_PROBE': str(self.probe),
            'FAKE_PYTHON': str(self.python),
            'BOOT_IMAGE': str(self.boot),
            'OUT_DIR': str(self.out),
            'GENERATOR_LOG': str(self.generator_log),
            'PROBE_OUTPUT': features,
            'PROBE_STATUS': str(status),
            'GENERATOR_STATUS': str(generator_status),
            'GENERATOR_OUTPUT': generator_output,
        })
        if policy is not None:
            env['BB_ISA_REWRITE'] = policy
        script = (
            'source "$ISA_REWRITE_HELPER"\n'
            'isa_rewrite_preflight_check "$FAKE_PROBE"\n'
            'isa_rewrite_preflight_generate "$FAKE_PYTHON" "$BOOT_IMAGE" "$OUT_DIR"\n'
            'printf \'%s\\n\' __ISA_ARGS__\n'
            'printf \'[%s]\\n\' "${ISA_REWRITE_ARGS[@]}"\n'
        )
        return subprocess.run(['bash', '-euo', 'pipefail', '-c', script], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=False)

    @staticmethod
    def args_from(result):
        return result.stdout.split('__ISA_ARGS__\n', 1)[1].splitlines()

    def test_missing_rewritable_features_generate_and_pass_table_path(self):
        result = self.run_preflight('CPU: missing MOVBE, BMI1, LZCNT')
        expected_table = str(self.out / 'isa-rewrite.bin')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args_from(result), ['[--isa-rewrite]', f'[{expected_table}]'])
        self.assertTrue((self.out / 'isa-rewrite.bin').is_file())
        self.assertIn('scripts/isa_rewrite.py', self.generator_log.read_text(encoding='utf-8'))

    def test_complete_cpu_does_not_generate_or_pass_table(self):
        result = self.run_preflight('CPU: complete')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args_from(result), ['[]'])
        self.assertFalse(self.generator_log.exists())

    def test_fatal_cpu_features_stop_before_generation(self):
        result = self.run_preflight(
            'STOP: this CPU lacks instruction sets the game uses: AVX', status=20)
        self.assertEqual(result.returncode, 20)
        self.assertIn('STOP: this CPU lacks instruction sets', result.stdout)
        self.assertFalse(self.generator_log.exists())

    def test_failed_generator_does_not_pass_stale_table(self):
        stale_table = self.out / 'isa-rewrite.bin'
        stale_table.write_bytes(b'old image table')
        result = self.run_preflight(
            'CPU: missing MOVBE, BMI1, LZCNT', generator_status=1,
            generator_output='ISA: rewrite table unavailable (iced_x86 is not installed)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args_from(result), ['[]'])
        self.assertEqual(stale_table.read_bytes(), b'old image table')
        self.assertIn('iced_x86 is not installed', result.stdout)

    def test_rewrite_policy_can_force_or_disable_generation(self):
        forced = self.run_preflight('CPU: complete', policy='1')
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(self.args_from(forced), ['[--isa-rewrite]', f'[{self.out / "isa-rewrite.bin"}]'])
        self.assertTrue(self.generator_log.exists())

        self.generator_log.unlink()
        (self.out / 'isa-rewrite.bin').unlink()
        disabled = self.run_preflight('CPU: missing MOVBE, BMI1, LZCNT', policy='0')
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(self.args_from(disabled), ['[]'])
        self.assertFalse(self.generator_log.exists())


if __name__ == '__main__':
    unittest.main()
