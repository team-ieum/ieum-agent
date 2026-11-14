"""DEV Secrets 명시 입력과 앱 기본값 보존을 합성 값으로 검증한다."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import agent_env


ROOT = Path(__file__).resolve().parents[2]
VALUE = 'synthetic-value'


def settings():
    source = {'DEV_COM_' + key: VALUE for key in agent_env.REQUIRED_KEYS}
    source['DEV_COM_PORT'] = '8000'
    return source


def rendered(source):
    content = agent_env.build_environment(agent_env.dev_environment_payload(source))
    return dict(line.split('=', 1) for line in content.decode().splitlines())


class DevExplicitSecretsTest(unittest.TestCase):
    def test_only_allowlisted_dev_inputs_are_selected_without_legacy_fallback(self):
        source = settings()
        source.update({'DEV_COM_NEW_OPTION': VALUE, 'MAIN_COM_NEW_OPTION': VALUE,
                       'AWS_NEW_OPTION': VALUE, agent_env.SECRETS_VARIABLE: 'not-json',
                       'DEV_COM_AGENT_TEST_MONGODB_URL': VALUE, 'DEV_COM_TEST_MONGODB_DB_NAME': VALUE,
                       'DEV_COM_AGENT_PYTHON_VERSION': VALUE, 'DEV_COM_IMAGE_NAME': VALUE})
        self.assertEqual(rendered(source), {key.removeprefix('DEV_COM_'): value for key, value in settings().items()})
        source.pop('DEV_COM_MONGODB_URL')
        source.update({'MAIN_COM_MONGODB_URL': VALUE, 'AWS_MONGODB_URL': VALUE,
                       agent_env.SECRETS_VARIABLE: json.dumps(settings())})
        with self.assertRaisesRegex(agent_env.AgentEnvironmentError, 'missing_required_configuration'):
            rendered(source)

    def test_each_required_input_rejects_missing_empty_or_whitespace(self):
        for key in agent_env.REQUIRED_KEYS:
            for value in (None, '', ' \t '):
                source = settings()
                name = 'DEV_COM_' + key
                source.pop(name)
                if value is not None:
                    source[name] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                        agent_env.AgentEnvironmentError, 'missing_required_configuration'):
                    rendered(source)

    def test_unregistered_optional_inputs_preserve_defaults_and_dev_token_policy(self):
        source = {'DEV_COM_' + key: '' for key in agent_env.DEV_RUNTIME_KEYS}
        source.update(settings())
        self.assertEqual(rendered(source), {key.removeprefix('DEV_COM_'): value for key, value in settings().items()})
        self.assertNotIn('AGENT_INTERNAL_TOKEN', rendered(source))
        source['DEV_COM_AGENT_INTERNAL_TOKEN'] = VALUE
        self.assertEqual(rendered(source)['AGENT_INTERNAL_TOKEN'], VALUE)

    def test_empty_alias_inputs_do_not_collide_but_two_nonempty_names_fail(self):
        for alias, target in agent_env.ALIASES.items():
            source = settings()
            source['DEV_COM_' + target] = ''
            source['DEV_COM_' + alias] = '8000' if target == 'PORT' else VALUE
            with self.subTest(alias=alias):
                self.assertEqual(rendered(source)[target], source['DEV_COM_' + alias])
                source['DEV_COM_' + target] = source['DEV_COM_' + alias]
                with self.assertRaisesRegex(agent_env.AgentEnvironmentError, 'duplicate_configuration_key'):
                    rendered(source)

    def test_all_current_app_settings_except_controller_owned_route_are_supported(self):
        tree = ast.parse((ROOT / 'core/config.py').read_text())
        settings_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Settings')
        fields = {node.target.id for node in settings_class.body
                  if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        self.assertTrue(fields - {'BACKEND_BASE_URL'} <= set(agent_env.DEV_RUNTIME_KEYS))
        self.assertTrue({'TAVILY_API_KEY', 'OTEL_EXPORTER_OTLP_ENDPOINT'} <= set(agent_env.DEV_RUNTIME_KEYS))
        self.assertNotIn('BACKEND_BASE_URL', agent_env.DEV_RUNTIME_KEYS)

    def test_literal_values_survive_without_shell_or_json_list_expansion(self):
        source = settings() | {
            'DEV_COM_TAVILY_API_KEY': '  literal = $HOME $(touch example) `value` "quoted"  ',
            'DEV_COM_SELF_HOSTED_ALLOWED_ROLES': '["ROLE_ADMIN", "ROLE_TESTER"]',
            'DEV_COM_SUPPORTED_PROVIDERS': '["OPENAI", "GEMINI"]',
        }
        for key in ('TAVILY_API_KEY', 'SELF_HOSTED_ALLOWED_ROLES', 'SUPPORTED_PROVIDERS'):
            self.assertEqual(rendered(source)[key], source['DEV_COM_' + key])
        for character in agent_env.FORBIDDEN_CHARACTERS:
            with self.subTest(character=repr(character)), self.assertRaisesRegex(
                    agent_env.AgentEnvironmentError, 'unsupported_control_character'):
                rendered(source | {'DEV_COM_TAVILY_API_KEY': VALUE + character})

    def test_cli_never_uses_full_secrets_json_or_another_namespace_as_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.env'
            candidate.touch(mode=0o600)
            child = {'PATH': os.defpath, agent_env.SECRETS_VARIABLE: json.dumps(settings())}
            child.update({key.replace('DEV_COM_', 'MAIN_COM_'): value for key, value in settings().items()})
            child.update({key.replace('DEV_COM_', 'AWS_'): value for key, value in settings().items()})
            if 'LD_LIBRARY_PATH' in os.environ:
                child['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
            result = subprocess.run([sys.executable, agent_env.__file__, str(candidate)],
                                    env=child, text=True, capture_output=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout), (1, ''))
            self.assertEqual(result.stderr, 'Agent environment rejected: missing_required_configuration\n')
            self.assertEqual(candidate.read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
