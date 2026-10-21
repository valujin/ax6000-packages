"""Native CI logging must retain failures with GitHub's default bash -e shell."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def workflow_build_block():
    lines = (ROOT / '.github/workflows/firmware.yml').read_text().splitlines()
    selected = False
    for index, line in enumerate(lines):
        if line.strip() == '- name: Build firmware, SDK and kernel modules':
            selected = True
        elif selected and line.strip() == 'run: |':
            indentation = len(line) - len(line.lstrip())
            body = []
            for candidate in lines[index + 1:]:
                if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indentation:
                    break
                body.append(candidate[indentation + 2:])
            return '\n'.join(body) + '\n'
        elif selected and re.match(r'\s*- (?:name|uses):', line):
            break
    raise AssertionError('Native firmware workflow build step is missing')


class FirmwareWorkflowTests(unittest.TestCase):
    def test_native_build_tee_preserves_child_status_under_github_bash_e(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'scripts'
            scripts.mkdir()
            stub = scripts / 'build-firmware.sh'
            stub.write_text('#!/usr/bin/env bash\nprintf "native-build-marker\\n"\nexit "$CHILD_STATUS"\n')
            environment = dict(os.environ, PROFILE='fixture-profile.json', RUNNER_TEMP=str(root))
            for inherited in ('SHELLOPTS', 'BASHOPTS', 'BASH_ENV'):
                environment.pop(inherited, None)
            # Do not add pipefail in this test's shell: the actual workflow must.
            for status in (0, 37):
                with self.subTest(status=status):
                    result = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-c', workflow_build_block()],
                        cwd=root, env=dict(environment, CHILD_STATUS=str(status)),
                        text=True, capture_output=True)
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertIn('native-build-marker', (root / 'logs/firmware-build.log').read_text())


if __name__ == '__main__':
    unittest.main()
