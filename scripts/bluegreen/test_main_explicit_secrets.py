"""MAIN의 명시 입력, 앱 기본값 및 DEV 전송 분리를 합성 값으로 검증한다."""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import agent_env


ROOT = Path(__file__).resolve().parents[2]
VALUE = 'synthetic-value'


def settings():
    source = {'MAIN_COM_' + key: VALUE for key in agent_env.REQUIRED_KEYS}
    source.update(MAIN_COM_PORT='8000', MAIN_COM_AGENT_INTERNAL_TOKEN=VALUE)
    return source


def rendered(source):
    content = agent_env.build_environment(agent_env.main_environment_payload(source), 'main')
    return dict(line.split('=', 1) for line in content.decode().splitlines())


class MainExplicitSecretsTest(unittest.TestCase):
    def test_unmapped_secrets_and_old_json_are_not_candidates_or_fallbacks(self):
        source = settings()
        source.update({'MAIN_COM_NEW_OPTION': VALUE, 'DEV_COM_NEW_OPTION': VALUE,
                       'AWS_NEW_OPTION': VALUE, agent_env.SECRETS_VARIABLE: 'not-json'})
        self.assertNotIn('NEW_OPTION', rendered(source))
        source.pop('MAIN_COM_MONGODB_URL')
        source.update({'DEV_COM_MONGODB_URL': VALUE, agent_env.SECRETS_VARIABLE: json.dumps(settings())})
        with self.assertRaisesRegex(agent_env.AgentEnvironmentError, 'missing_required_configuration'):
            rendered(source)

    def test_required_missing_empty_or_whitespace_is_rejected(self):
        for key in agent_env.REQUIRED_KEYS + ('AGENT_INTERNAL_TOKEN',):
            for value in (None, '', ' \t '):
                source = settings()
                name = 'MAIN_COM_' + key
                source.pop(name)
                if value is not None:
                    source[name] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                        agent_env.AgentEnvironmentError, 'missing_required_configuration'):
                    rendered(source)

    def test_missing_optional_inputs_keep_application_defaults(self):
        source = {'MAIN_COM_' + key: '' for key in agent_env.MAIN_RUNTIME_KEYS}
        source.update(settings())
        self.assertEqual(rendered(source), {key.removeprefix('MAIN_COM_'): value for key, value in settings().items()})
        for name in ('SELF_HOSTED_ALLOWED_ROLES', 'SUPPORTED_PROVIDERS', 'AGENT_TIMEOUT_SECONDS'):
            self.assertNotIn(name, rendered(source))

    def test_aliases_accept_empty_direct_names_but_nonempty_collisions_fail(self):
        for alias, target in agent_env.ALIASES.items():
            source = settings()
            source['MAIN_COM_' + target] = ''
            source['MAIN_COM_' + alias] = '8000' if target == 'PORT' else VALUE
            with self.subTest(alias=alias):
                self.assertEqual(rendered(source)[target], source['MAIN_COM_' + alias])
                source['MAIN_COM_' + target] = source['MAIN_COM_' + alias]
                with self.assertRaisesRegex(agent_env.AgentEnvironmentError, 'duplicate_configuration_key'):
                    rendered(source)

    def test_literal_values_are_preserved_and_record_separators_rejected(self):
        source = settings()
        source['MAIN_COM_TAVILY_API_KEY'] = '  literal = $HOME $(touch example) `value` "quoted"  '
        self.assertEqual(rendered(source)['TAVILY_API_KEY'], source['MAIN_COM_TAVILY_API_KEY'])
        for character in agent_env.FORBIDDEN_CHARACTERS:
            with self.subTest(character=repr(character)), self.assertRaisesRegex(
                    agent_env.AgentEnvironmentError, 'unsupported_control_character'):
                rendered(source | {'MAIN_COM_TAVILY_API_KEY': VALUE + character})

    def test_all_app_settings_are_supported_except_controller_owned_route(self):
        tree = ast.parse((ROOT / 'core/config.py').read_text())
        settings_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Settings')
        fields = {node.target.id for node in settings_class.body
                  if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        self.assertTrue(fields - {'BACKEND_BASE_URL'} <= set(agent_env.MAIN_RUNTIME_KEYS))
        self.assertTrue({'TAVILY_API_KEY', 'OTEL_EXPORTER_OTLP_ENDPOINT'} <= set(agent_env.MAIN_RUNTIME_KEYS))
        self.assertNotIn('BACKEND_BASE_URL', agent_env.MAIN_RUNTIME_KEYS)

    def test_deploy_and_stage_pass_exactly_the_supported_inputs(self):
        workflow = (ROOT / '.github/workflows/agent-main-server.yml').read_text()
        self.assertNotIn('toJSON(secrets)', workflow)
        expected = {'MAIN_COM_' + key for key in agent_env.MAIN_RUNTIME_KEYS}
        for step in ('Deploy with Main prefixed Secrets', 'Stage Main bootstrap settings'):
            body = workflow.split('      - name: ' + step + '\n', 1)[1].split('      - name:', 1)[0]
            pairs = re.findall(r'^          (MAIN_COM_[A-Z0-9_]+): \$\{\{ secrets\.([A-Z0-9_]+) \}\}$', body, re.MULTILINE)
            with self.subTest(step=step):
                self.assertEqual(len(pairs), len(expected))
                self.assertEqual({name for name, _ in pairs}, expected)
                self.assertTrue(all(name == secret for name, secret in pairs))
                self.assertLess(body.index('for secret_name in "${!MAIN_COM_@}"'),
                                body.index('python3 /home/kwung/deploy/bluegreen-main/bluegreen.py'))

    def test_main_cli_never_reads_legacy_json_as_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.env'
            candidate.touch(mode=0o600)
            child = {'PATH': os.defpath, agent_env.SECRETS_VARIABLE: json.dumps(settings())}
            if 'LD_LIBRARY_PATH' in os.environ:
                child['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
            result = subprocess.run([sys.executable, agent_env.__file__, str(candidate), '--environment', 'main'],
                                    env=child, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, 'Agent environment rejected: missing_required_configuration\n')
            self.assertEqual(candidate.read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
