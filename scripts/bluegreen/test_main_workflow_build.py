"""작업 브랜치의 MAIN CI는 GitHub build만 허용하고 서버 job은 차단합니다."""

import ast
import itertools
from pathlib import Path
import re
import unittest

APP = 'agent'
BRANCH = 'infra/IEUM-AI-111'
ROOT = Path(__file__).resolve().parents[2]


class MainBuildOnlyBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / '.github/workflows' / (APP + '-main-server.yml')).read_text()

    def enabled(self, job, ref, event, operation, deploy='false', bootstrap='false'):
        section = self.workflow.split('  ' + job + ':\n', 1)[1]
        match = re.search(r'^    if: (.+)(?:\n|$)', section, re.MULTILINE)
        self.assertIsNotNone(match)
        condition = match[1]
        if condition == '>-':
            lines = []
            for line in section[match.end():].splitlines():
                if not line.startswith('      '):
                    break
                lines.append(line.strip())
            condition = ' '.join(lines)
        for name, value in {
            'github.ref': ref, 'github.event_name': event, 'inputs.operation': operation,
            'vars.MAIN_DEPLOY_ENABLED': deploy, 'vars.MAIN_BOOTSTRAP_ENABLED': bootstrap,
        }.items():
            condition = condition.replace(name, repr(value))
        expression = ast.parse(condition.replace('&&', ' and ').replace('||', ' or '), mode='eval')
        allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.Constant)
        self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(expression)))
        return eval(compile(expression, '<main-workflow-condition>', 'eval'), {'__builtins__': {}})

    def test_work_branch_manual_build_is_the_only_non_main_job_allowed(self):
        refs = ('refs/heads/' + BRANCH, 'refs/heads/dev', 'refs/heads/infra/other', 'refs/tags/main')
        events = ('workflow_dispatch', 'push', 'pull_request')
        operations = ('build', 'stage-bootstrap', 'deploy', '')
        for ref, event, operation in itertools.product(refs, events, operations):
            with self.subTest(ref=ref, event=event, operation=operation):
                self.assertEqual(self.enabled('build', ref, event, operation),
                    ref == 'refs/heads/' + BRANCH and event == 'workflow_dispatch' and operation == 'build')
                # 두 관리 flag를 모두 켜도 작업 브랜치에서 서버 job은 시작되지 않아야 합니다.
                for job in ('deploy', 'stage_bootstrap'):
                    self.assertFalse(self.enabled(job, ref, event, operation, 'true', 'true'))

    def test_main_preserves_deploy_and_bootstrap_gates(self):
        cases = (('push', ''), ('workflow_dispatch', 'build'),
                 ('workflow_dispatch', 'stage-bootstrap'), ('workflow_dispatch', 'deploy'))
        for (event, operation), deploy, bootstrap in itertools.product(cases, ('false', 'true'), ('false', 'true')):
            with self.subTest(event=event, operation=operation, deploy=deploy, bootstrap=bootstrap):
                self.assertTrue(self.enabled('build', 'refs/heads/main', event, operation, deploy, bootstrap))
                self.assertEqual(self.enabled('deploy', 'refs/heads/main', event, operation, deploy, bootstrap),
                    deploy == 'true' and (event == 'push' or operation == 'deploy'))
                self.assertEqual(self.enabled('stage_bootstrap', 'refs/heads/main', event, operation, deploy, bootstrap),
                    event == 'workflow_dispatch' and operation == 'stage-bootstrap' and bootstrap == 'true')

    def test_build_lock_is_separate_and_push_remains_main_only(self):
        self.assertIn('push:\n    branches: [main]', self.workflow)
        self.assertIn("github.ref == 'refs/heads/main' && '" + APP + "_main_server'", self.workflow)
        self.assertIn("format('" + APP + "_main_build_{0}', github.ref)", self.workflow)
        build = self.workflow.split('  deploy:\n', 1)[0]
        self.assertIn('runs-on: ubuntu-latest', build)
        self.assertNotIn('self-hosted', build)
        self.assertNotIn('toJSON(secrets)', build)
        self.assertIn('name: Verify packaged image on GitHub runner', build)
        self.assertLess(build.index('main_image.py load'), build.index('uses: actions/upload-artifact@v4'))

    def test_deploy_and_bootstrap_use_the_verified_loaded_id(self):
        deploy, bootstrap = self.workflow.split('  stage_bootstrap:\n', 1)
        deploy = deploy.split('  deploy:\n', 1)[1]
        for section, control in ((deploy, 'Deploy with Main prefixed Secrets'), (bootstrap, 'Stage Main bootstrap settings')):
            with self.subTest(control=control):
                verify, after = section.split('      - name: Verify archive and loaded image identity\n', 1)
                loaded, command = after.split('      - name: ' + control + '\n', 1)
                self.assertIn('id: loaded_image', loaded)
                self.assertIn('IMAGE_ID: ${{ needs.build.outputs.image_id }}', loaded)
                self.assertIn('--image-id "$IMAGE_ID" --sha256 "$ARCHIVE_SHA256"', loaded)
                self.assertIn('IMAGE_ID: ${{ steps.loaded_image.outputs.image_id }}', command)
                self.assertNotIn('needs.build.outputs.image_id', command)
                self.assertIn('--service ' + APP + ' --image "$IMAGE_ID"', command)


if __name__ == '__main__':
    unittest.main()

