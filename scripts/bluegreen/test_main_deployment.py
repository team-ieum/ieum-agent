"""Main namespace, image artifact integrity and workflow boundaries; synthetic inputs only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from test_main_image_archive import classic_archive_entries, oci_archive_entries, write_archive

APP = 'agent'
ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


env = load('main_' + APP + '_env', APP + '_env.py')
image = load('main_' + APP + '_image', 'main_image.py')
REFERENCE = 'ieum-' + APP + '-main:' + 'a' * 40
IDENTITY = 'sha256:' + 'b' * 64
SECRET = 'synthetic-main-value'


def payload(extra=None):
    values = {key: SECRET for key in env.REQUIRED_KEYS}
    if APP == 'agent':
        values.update(PORT='8000', AGENT_INTERNAL_TOKEN=SECRET)
    return json.dumps({'MAIN_COM_' + key: value for key, value in values.items()} | (extra or {}))


def records(value):
    return dict(line.split('=', 1) for line in value.decode().splitlines())


class MainEnvironmentTest(unittest.TestCase):
    def test_main_defaults_and_no_cross_environment_value_leak(self):
        data = json.loads(payload())
        data.update({'DEV_COM_NEW_OPTION': SECRET, 'AWS_NEW_OPTION': SECRET,
                     'MAIN_BLUE_NEW_OPTION': SECRET, 'MAIN_GREEN_NEW_OPTION': SECRET})
        actual = records(env.build_environment(json.dumps(data), 'main'))
        self.assertNotIn('NEW_OPTION', actual)
        self.assertEqual(actual['SPRING_PROFILES_ACTIVE'] if APP == 'backend' else actual['PORT'],
                         'main' if APP == 'backend' else '8000')

    def test_dev_configuration_never_fills_missing_main_values(self):
        values = json.loads(payload())
        for key in list(values):
            incomplete = dict(values)
            incomplete['DEV_COM_' + key[len('MAIN_COM_'):]] = incomplete.pop(key)
            with self.subTest(key=key), self.assertRaises(env.__dict__[APP.title() + 'EnvironmentError']):
                env.build_environment(json.dumps(incomplete), 'main')

    def test_main_aliases_and_dynamic_values_are_preserved(self):
        values = json.loads(payload({'MAIN_COM_NEW_OPTION': '  literal = $HOME `value`  '}))
        for alias, target in env.ALIASES.items():
            target_name = 'MAIN_COM_' + target
            if target_name in values:
                values['MAIN_COM_' + alias] = values.pop(target_name)
        actual = records(env.build_environment(json.dumps(values), 'main'))
        self.assertEqual(actual['NEW_OPTION'], '  literal = $HOME `value`  ')
        self.assertEqual(actual['DB_PASSWORD' if APP == 'backend' else 'MONGODB_URL'], SECRET)

    def test_main_build_metadata_does_not_enter_runtime(self):
        extra = {'MAIN_COM_' + key: 'build-only' for key in env.MAIN_BUILD_KEYS}
        actual = records(env.build_environment(payload(extra), 'main'))
        for key in env.MAIN_BUILD_KEYS:
            self.assertNotIn(key, actual)

    def test_reserved_routes_are_still_rejected(self):
        route = 'AGENT_SERVICE_URL' if APP == 'backend' else 'BACKEND_BASE_URL'
        for key in (route, route.replace('_', '')):
            with self.subTest(key=key), self.assertRaises(env.__dict__[APP.title() + 'EnvironmentError']):
                env.build_environment(payload({'MAIN_COM_' + key: 'untrusted-route'}), 'main')

    def test_main_defaults_cannot_select_dev_or_disable_internal_auth(self):
        values = json.loads(payload())
        if APP == 'backend':
            for key in ('SPRING_PROFILES_ACTIVE', 'SPRINGPROFILES_ACTIVE'):
                with self.subTest(key=key), self.assertRaises(env.BackendEnvironmentError):
                    env.build_environment(payload({'MAIN_COM_' + key: 'dev'}), 'main')
        else:
            for value in ('', '   '):
                with self.subTest(value=value), self.assertRaises(env.AgentEnvironmentError):
                    env.build_environment(payload({'MAIN_COM_AGENT_INTERNAL_TOKEN': value}), 'main')

    def test_invalid_environment_is_rejected(self):
        with self.assertRaises(env.__dict__[APP.title() + 'EnvironmentError']):
            env.build_environment(payload(), 'unknown')

    def test_main_cli_writes_private_file_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.env'
            candidate.touch(mode=0o600)
            child_env = {'PATH': os.defpath, **json.loads(payload())}
            if 'LD_LIBRARY_PATH' in os.environ:
                child_env['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
            result = subprocess.run([sys.executable, str(Path(env.__file__)), str(candidate), '--environment', 'main'],
                                    env=child_env, capture_output=True, text=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            self.assertEqual(candidate.read_bytes(), env.build_environment(payload(), 'main'))


class MainImageArtifactTest(unittest.TestCase):
    def test_build_metadata_requires_main_image_and_supported_runtime(self):
        runtime = {'JAVA_VERSION': '21.0.11'} if APP == 'backend' else {'PYTHON_VERSION': '3.11.15'}
        self.assertEqual(image.validate_build({'IMAGE_NAME': 'ieum-' + APP + '-main', **runtime}),
                         '21' if APP == 'backend' else '3.11')
        for name in ('ieum-' + APP + '-dev', 'main;touch-file', '', 'UPPER-main', 'ieum-dev-main'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                image.validate_build({'IMAGE_NAME': name, **runtime})

    def test_bad_archive_digest_never_calls_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.tar'
            path.write_bytes(b'corrupt-image')
            with patch.object(image, 'run') as run, self.assertRaisesRegex(ValueError, 'digest_mismatch'):
                image.load_image(path, REFERENCE, IDENTITY, 'a' * 64)
            run.assert_not_called()

    def test_load_checks_image_identity_after_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.tar'
            config_id, entries = classic_archive_entries(REFERENCE)
            write_archive(path, entries)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(image, 'run', return_value=''), patch.object(image, 'image_id', return_value=config_id):
                self.assertEqual(image.load_image(path, REFERENCE, config_id, digest), config_id)
            wrong_ids, wrong_entries = oci_archive_entries(REFERENCE, configuration={
                'architecture': 'amd64', 'os': 'linux', 'config': {'Env': ['WRONG=true']}})
            def docker(arguments):
                if arguments[:3] == ['docker', 'image', 'save']:
                    write_archive(Path(arguments[4]), wrong_entries)
                return ''
            with patch.object(image, 'run', side_effect=docker), \
                    patch.object(image, 'image_id', return_value=wrong_ids['image_id']), \
                    self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                image.load_image(path, REFERENCE, config_id, digest)

    def test_symlink_archive_is_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.tar'
            target = Path(directory) / 'target'
            target.write_bytes(b'synthetic-image')
            path.symlink_to(target)
            with patch.object(image, 'run') as run, self.assertRaises(ValueError):
                image.load_image(path, REFERENCE, IDENTITY, hashlib.sha256(target.read_bytes()).hexdigest())
            run.assert_not_called()

    def test_package_exports_only_the_named_image_and_records_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o700)
            path = parent / 'image.tar'
            config_id, entries = classic_archive_entries(REFERENCE)
            def save(arguments):
                self.assertEqual(arguments, ['docker', 'image', 'save', '--output', str(path), REFERENCE])
                write_archive(path, entries)
                return ''
            with patch.object(image, 'run', side_effect=save), patch.object(image, 'image_id', return_value=config_id):
                metadata = image.package_image(path, REFERENCE)
            self.assertEqual(metadata, {'image_id': config_id,
                                       'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    def test_mutable_tags_and_replaced_image_are_rejected(self):
        for reference in ('ieum-' + APP + '-main:latest', 'ieum-' + APP + '-dev:' + 'a' * 40):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                image.validate_image(reference)
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory); parent.chmod(0o700)
            with patch.object(image, 'run', return_value=''), \
                    patch.object(image, 'image_id', side_effect=[IDENTITY, 'sha256:' + 'c' * 64]), \
                    self.assertRaisesRegex(ValueError, 'changed_during_packaging'):
                image.package_image(parent / 'image.tar', REFERENCE)


class MainWorkflowBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / '.github/workflows' / (APP + '-main-server.yml')).read_text()

    def test_main_build_can_run_while_deploy_is_default_off(self):
        guard = "if: github.ref == 'refs/heads/main' && vars.MAIN_DEPLOY_ENABLED == 'true'"
        self.assertEqual(self.source.count(guard), 1)
        build = self.source.split("  deploy:\n", 1)[0]
        self.assertIn("github.ref == 'refs/heads/main' ||", build)
        self.assertIn("github.event_name == 'push' || inputs.operation == 'deploy'", self.source)
        self.assertIn('options: [build, stage-bootstrap, deploy]', self.source)
        self.assertIn('default: build', self.source)
        self.assertIn("inputs.operation == 'stage-bootstrap' && vars.MAIN_BOOTSTRAP_ENABLED == 'true'", self.source)
        self.assertIn('runs-on: ubuntu-latest', self.source)
        self.assertIn('group: ieum_main_server', self.source)
        self.assertIn('labels: [self-hosted, linux, x64, ieum-' + APP + ', main, deploy]', self.source)

    def test_build_receives_only_build_metadata_and_uploads_only_image(self):
        build, deploy = self.source.split('  deploy:\n', 1)
        expected = {'MAIN_COM_JAVA_VERSION', 'MAIN_COM_IMAGE_NAME'} if APP == 'backend' else \
                   {'MAIN_COM_AGENT_PYTHON_VERSION', 'MAIN_COM_AGENT_IMAGE_NAME'}
        self.assertEqual(set(re.findall(r'secrets\.([A-Z0-9_]+)', build)), expected)
        self.assertNotIn('toJSON(secrets)', build)
        self.assertIn('path: ${{ steps.package.outputs.directory }}/image.tar', build)
        self.assertIn('artifact-ids: ${{ needs.build.outputs.artifact_id }}', deploy)
        for key in ('archive_sha256', 'image_id'):
            self.assertIn('needs.build.outputs.' + key, deploy)
        self.assertNotIn('image_ref', self.source)
        self.assertIn('IMAGE_NAME: ${{ secrets.' + ('MAIN_COM_IMAGE_NAME' if APP == 'backend' else 'MAIN_COM_AGENT_IMAGE_NAME') + ' }}', deploy)
        self.assertIn('--image \"$IMAGE_NAME:$IMAGE_TAG\"', deploy)

    def test_main_routes_and_main_sha_variables_are_isolated(self):
        self.assertNotIn('AWS_', self.source)
        self.assertNotIn('/home/kwung/deploy/bluegreen/bluegreen.py', self.source)
        self.assertIn('/home/kwung/deploy/bluegreen-main/bluegreen.py', self.source)
        self.assertIn('/usr/local/sbin/ieum-main-bluegreen-nginx', self.source)
        self.assertIn('IEUM_DEPLOY_ENVIRONMENT: main', self.source)
        self.assertIn('vars.MAIN_BLUEGREEN_CONTROLLER_SHA256', self.source)
        self.assertIn('vars.MAIN_BLUEGREEN_HELPER_SHA256', self.source)
        self.assertNotIn('APP_MONGODB_URL:', self.source)
        self.assertNotIn('secrets.MAIN_COM_AGENT_TEST_MONGODB_URL', self.source)

    def test_main_secret_candidate_is_created_only_after_image_validation(self):
        marker = '      - name: Deploy with Main prefixed Secrets\n'
        self.assertNotIn('toJSON(secrets)', self.source)
        before, step = self.source.split(marker, 1)
        self.assertNotIn('secrets.MAIN_COM_AGENT_INTERNAL_TOKEN', before)
        self.assertIn('name: Verify archive and loaded image identity', before)
        self.assertIn('--environment main', step)
        self.assertIn('unset secret_name ' + env.SECRETS_VARIABLE, step)
        self.assertIn('--service ' + APP + ' --image "$IMAGE_ID"', step)

    def test_bootstrap_staging_is_explicitly_gated_and_does_not_deploy(self):
        stage = self.source.split('  stage_bootstrap:\n', 1)[1]
        self.assertIn("inputs.operation == 'stage-bootstrap' && vars.MAIN_BOOTSTRAP_ENABLED == 'true'", stage)
        self.assertIn('--lock-timeout 30 stage-bootstrap', stage)
        self.assertIn('--environment main', stage)
        self.assertNotIn('toJSON(secrets)', stage)
        self.assertNotIn('--replace-env', stage)
        self.assertNotIn('--job-deadline-epoch', stage)
        self.assertNotIn('docker run', stage)
        self.assertIn('artifact-ids: ${{ needs.build.outputs.artifact_id }}', stage)
        self.assertIn('--image-id "$IMAGE_ID" --sha256 "$ARCHIVE_SHA256"', stage)

    def test_main_secret_shell_preserves_values_cleans_candidate_and_strips_inputs(self):
        body = self.source.split('      - name: Deploy with Main prefixed Secrets\n', 1)[1]
        body = body.split('      - name:', 1)[0].split('        run: |\n', 1)[1]
        shell = textwrap.dedent(body)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_file = root / 'result.json'
            controller = root / 'controller.py'
            controller.write_text('import json,os,pathlib,sys\n'
                'args=sys.argv[1:]\n'
                'path=pathlib.Path(args[args.index("--env-file")+1])\n'
                'pathlib.Path(' + repr(str(result_file)) + ').write_text(json.dumps({'
                '"values":path.read_text(),"json_inherited":' + repr(env.SECRETS_VARIABLE) + ' in os.environ,'
                '"main_inputs_inherited":any(k.startswith("MAIN_COM_") for k in os.environ),'
                '"deployment_environment":os.environ.get("IEUM_DEPLOY_ENVIRONMENT"),"args":args}))\n')
            shell = shell.replace('/home/kwung/deploy/bluegreen-main/bluegreen.py', shlex.quote(str(controller)))
            shell = shell.replace('scripts/bluegreen/' + APP + '_env.py', shlex.quote(str(Path(env.__file__))))
            child_env = {'PATH': str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                         'RUNNER_TEMP': str(root), **json.loads(payload()), 'IMAGE_ID': IDENTITY,
                         env.SECRETS_VARIABLE: 'must-not-be-read',
                         'MAIN_COM_TAVILY_API_KEY': '  literal = $HOME `value`  ',
                         'MAIN_COM_UNMAPPED_SECRET': SECRET,
                         'IEUM_DEPLOY_ENVIRONMENT': 'main', 'IEUM_BLUEGREEN_JOB_DEADLINE_EPOCH': '9999999999'}
            if 'LD_LIBRARY_PATH' in os.environ:
                child_env['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
            result = subprocess.run(['bash', '-c', shell], env=child_env, cwd=root,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            observed = json.loads(result_file.read_text())
            self.assertFalse(observed['json_inherited'])
            self.assertFalse(observed['main_inputs_inherited'])
            self.assertEqual(observed['deployment_environment'], 'main')
            self.assertIn('DB_PASSWORD' if APP == 'backend' else 'MONGODB_URL', observed['values'])
            self.assertIn('TAVILY_API_KEY=  literal = $HOME `value`  \n', observed['values'])
            self.assertNotIn('UNMAPPED_SECRET', observed['values'])
            self.assertNotIn(SECRET, ' '.join(observed['args']))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['controller.py', 'result.json'])


if __name__ == '__main__':
    unittest.main()
