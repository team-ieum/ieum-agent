"""Agent DEV_ settings and workflow regressions; synthetic data only."""

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('agent_env.py')
REPOSITORY = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY / '.github/workflows/agent-dev-server.yml'
SECRET = 'synthetic-credential-never-print-this'
PREFIX = 'DEV_'
TRANSPORT = 'IEUM_AGENT_SECRETS_JSON'
DEFAULT_PAYLOAD = object()
spec = importlib.util.spec_from_file_location('agent_env_under_test', SCRIPT)
agent_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_env)
EXPECTED_ALIASES = {
    'AGENT_MONGODB_URL': 'MONGODB_URL', 'AGENT_MONGODB_DB_NAME': 'MONGODB_DB_NAME',
    'AGENT_PORT': 'PORT', 'AGENT_CLAUDE_DEFAULT_MODEL': 'CLAUDE_DEFAULT_MODEL',
    'AGENT_OPENAI_DEFAULT_MODEL': 'OPENAI_DEFAULT_MODEL',
    'AGENT_GEMINI_DEFAULT_MODEL': 'GEMINI_DEFAULT_MODEL',
    'AGENT_NOTION_MCP_URL': 'NOTION_MCP_URL', 'AGENT_NOTION_MCP_AUTH_TOKEN': 'NOTION_MCP_AUTH_TOKEN',
}


def valid_env():
    values = {key: 'synthetic-' + key.lower() for key in agent_env.REQUIRED_KEYS}
    values['MONGODB_URL'] = SECRET
    values['PORT'] = '8000'
    return values


def secret_data(values=None):
    return {PREFIX + key: value for key, value in (valid_env() if values is None else values).items()}


def secret_payload(values=None, extra=None):
    data = secret_data(values)
    data.update(extra or {})
    return json.dumps(data, ensure_ascii=True)


def records(content):
    return dict(line.split('=', 1) for line in content.decode('utf-8').splitlines())


class BuildEnvironmentTest(unittest.TestCase):
    def assert_rejected(self, payload, code=None):
        with self.assertRaises(agent_env.AgentEnvironmentError) as caught:
            agent_env.build_environment(payload)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertRegex(str(caught.exception), r'\A[a-z_]+\Z')
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_eight_aliases_and_direct_keys_preserve_existing_agent_settings(self):
        values = valid_env()
        values.update({key: SECRET for key in agent_env.OPTIONAL_KEYS})
        data = secret_data(values)
        for alias, target in EXPECTED_ALIASES.items():
            data[PREFIX + alias] = data.pop(PREFIX + target)
        self.assertEqual(agent_env.ALIASES, EXPECTED_ALIASES)
        self.assertEqual(records(agent_env.build_environment(json.dumps(data))), values)

    def test_each_required_key_rejects_missing_empty_or_whitespace_values(self):
        for key in agent_env.REQUIRED_KEYS:
            for value in (None, '', ' \t '):
                with self.subTest(key=key, kind='required missing'):
                    values = valid_env()
                    if value is None: del values[key]
                    else: values[key] = value
                    self.assert_rejected(secret_payload(values), 'missing_required_configuration')

    def test_new_keys_and_explicit_new_empty_values_are_accepted_without_mapping_changes(self):
        values = valid_env()
        values.update({'NEW_SERVICE_TOKEN':SECRET, 'NEW_EMPTY_OPTION':'', 'AGENT_TIMEOUT_SECONDS':'120'})
        self.assertEqual(records(agent_env.build_environment(secret_payload(values))), values)

    def test_known_empty_optional_values_are_omitted_and_deleted_keys_do_not_survive(self):
        values = valid_env()
        values.update({'NOTION_MCP_AUTH_TOKEN':SECRET, 'NEW_SERVICE_TOKEN':SECRET})
        first=records(agent_env.build_environment(secret_payload(values)))
        values['NOTION_MCP_AUTH_TOKEN']=''
        del values['NEW_SERVICE_TOKEN']
        second=records(agent_env.build_environment(secret_payload(values)))
        self.assertIn('NEW_SERVICE_TOKEN',first)
        self.assertNotIn('NOTION_MCP_AUTH_TOKEN',second)
        self.assertNotIn('NEW_SERVICE_TOKEN',second)

    def test_only_dev_prefix_is_selected_and_no_old_source_is_a_fallback(self):
        extra={'GITHUB_TOKEN':SECRET,'AWS_SECRET_ACCESS_KEY':SECRET,'MAIN_MONGODB_URL':SECRET,
               'AGENT_MONGODB_URL':SECRET,'DEVELOPMENT_TOKEN':SECRET,'DEVX_TOKEN':SECRET,
               'AGENT_ENV':'MONGODB_URL='+SECRET}
        self.assertEqual(records(agent_env.build_environment(secret_payload(extra=extra))), valid_env())
        self.assert_rejected(json.dumps({'AGENT_MONGODB_URL':SECRET,'AGENT_ENV':SECRET}))

    def test_test_database_and_fixed_build_metadata_never_enter_runtime_candidate(self):
        extras={PREFIX+key:SECRET for key in ('AGENT_TEST_MONGODB_URL','AGENT_TEST_MONGODB_DB_NAME',
                'TEST_MONGODB_URL','TEST_MONGODB_DB_NAME','PYTHON_VERSION','IMAGE_NAME',
                'AGENTTESTMONGODBURL','TEST_MONGO_DB_URL','IMAGE__NAME')}
        self.assertEqual(records(agent_env.build_environment(secret_payload(extra=extras))),valid_env())

    def test_case_is_normalized_but_source_names_and_suffixes_must_be_ascii_env_names(self):
        data={key.lower():value for key,value in secret_data().items()}
        data['Dev_New_Token']=SECRET
        self.assertEqual(records(agent_env.build_environment(json.dumps(data))), {**valid_env(),'NEW_TOKEN':SECRET})
        for suffix in ('','1KEY','KEY-NAME','KEY SPACE','서울','ß','KEY\nBAD'):
            with self.subTest(kind='invalid selected name'):
                self.assert_rejected(secret_payload(extra={PREFIX+suffix:SECRET}),'invalid_secret_name')

    def test_duplicate_case_alias_and_relaxed_names_are_rejected_even_when_optional_empty(self):
        for alias,target in EXPECTED_ALIASES.items():
            values=valid_env(); values[target]=SECRET
            for duplicate in ('',SECRET):
                with self.subTest(kind='alias collision',alias=alias):
                    self.assert_rejected(secret_payload(values,{PREFIX+alias:duplicate}),'duplicate_configuration_key')
        for suffix in ('mongodb_url','MONGODBURL','MONGO_DB_URL'):
            self.assert_rejected(secret_payload(extra={PREFIX+suffix:SECRET}),'duplicate_configuration_key')
        values=valid_env(); values['NOTION_MCP_AUTH_TOKEN']=''
        self.assert_rejected(secret_payload(values,{PREFIX+'AGENT_NOTION_MCP_AUTH_TOKEN':SECRET}),
                             'duplicate_configuration_key')

    def test_exact_duplicate_json_names_are_rejected(self):
        data=secret_payload()
        self.assert_rejected(data[:-1]+',"DEV_PORT":"8000"}', 'duplicate_secret_name')

    def test_controller_and_process_bootstrap_keys_cannot_override_runtime(self):
        for key in agent_env.RESERVED_KEYS+('IEUM_DEPLOYMENT_FUTURE_CONTROL',):
            for suffix in (key,key.lower(),key.replace('_','')):
                for value in ('',SECRET):
                    with self.subTest(key=key,kind='reserved control'):
                        self.assert_rejected(secret_payload(extra={PREFIX+suffix:value}),'reserved_configuration')

    def test_port_cannot_change_the_fixed_health_endpoint_or_add_uvicorn_arguments(self):
        for value in ('8001','08000','8000 --reload','$(touch injected)','8000; echo injected'):
            values=valid_env();values['PORT']=value
            self.assert_rejected(secret_payload(values),'invalid_port')

    def test_all_record_separators_are_rejected_for_required_optional_and_new_values(self):
        for key in ('MONGODB_URL','NOTION_MCP_AUTH_TOKEN','NEW_OPTION'):
            for control in ('\x00','\n','\r','\v','\f','\x1c','\x1d','\x1e','\x85','\u2028','\u2029'):
                values=valid_env();values[key]=SECRET+control+'INJECTED=other'
                self.assert_rejected(secret_payload(values),'unsupported_control_character')

    def test_values_are_literal_and_never_trimmed_or_evaluated(self):
        values=valid_env()
        values['NEW_OPTION']='  서울 '+SECRET+'=x # literal "${HOME}" $(echo x) `echo y`  '
        self.assertEqual(records(agent_env.build_environment(secret_payload(values))),values)

    def test_malformed_nonobject_nonstring_and_deep_json_are_sanitized(self):
        for payload in (None,'',{},b'{}','[','{'+SECRET,'[]','null','true','123',json.dumps(SECRET),
                        '{"DEV_NESTED":'+'['*1500+'0'+']'*1500+'}'):
            self.assert_rejected(payload)
        for value in (None,0,True,[],{}, {'nested':SECRET}):
            values=valid_env();values['MONGODB_URL']=value
            self.assert_rejected(secret_payload(values),'invalid_configuration')
        for constant in ('NaN','Infinity','-Infinity'):
            self.assert_rejected(secret_payload()[:-1]+',"UNSELECTED":'+constant+'}','invalid_json')

    def test_invalid_raw_and_selected_utf8_are_sanitized(self):
        self.assert_rejected(secret_payload()+'\ud800','invalid_encoding')
        values=valid_env();values['MONGODB_URL']=SECRET+'\ud800'
        self.assert_rejected(secret_payload(values),'invalid_encoding')

    def test_whole_json_size_limit_counts_unselected_values_and_utf8_bytes(self):
        data=secret_data();data['UNSELECTED']=''
        overhead=len(json.dumps(data).encode())
        data['UNSELECTED']='x'*(agent_env.MAX_JSON_BYTES-overhead)
        self.assertEqual(records(agent_env.build_environment(json.dumps(data))),valid_env())
        data['UNSELECTED']+='x'
        self.assert_rejected(json.dumps(data),'secrets_payload_too_large')
        data['UNSELECTED']='한'*(agent_env.MAX_JSON_BYTES//2)
        self.assert_rejected(json.dumps(data,ensure_ascii=False),'secrets_payload_too_large')

    def test_candidate_size_limit_and_sorted_record_order(self):
        values=valid_env()
        overhead=len(agent_env.build_environment(secret_payload(values)))+len('NEW_OPTION=\n')
        values['NEW_OPTION']='x'*(agent_env.MAX_ENV_BYTES-overhead)
        self.assertEqual(len(agent_env.build_environment(secret_payload(values))),agent_env.MAX_ENV_BYTES)
        values['NEW_OPTION']+='x'
        self.assert_rejected(secret_payload(values),'configuration_too_large')
        output=agent_env.build_environment(secret_payload())
        self.assertEqual(list(records(output)),sorted(valid_env()))


class WriteCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.candidate = self.root / 'candidate.env'
        self.candidate.touch(mode=0o600)
        self.candidate.chmod(0o600)

    def assert_private_rejection(self, destination, payload=DEFAULT_PAYLOAD):
        with self.assertRaises(agent_env.AgentEnvironmentError) as caught:
            agent_env.write_candidate(destination, secret_payload() if payload is DEFAULT_PAYLOAD else payload)
        self.assertNotIn(SECRET, str(caught.exception))

    def invoke_cli(self, payload=DEFAULT_PAYLOAD, destination=None):
        environment = {'PATH': os.defpath}
        if payload is DEFAULT_PAYLOAD:
            payload = secret_payload()
        if payload is not None:
            environment[TRANSPORT] = payload
        return subprocess.run([sys.executable, str(SCRIPT), str(destination or self.candidate)],
            env=environment, text=True, capture_output=True, timeout=3)

    def test_writes_canonical_bytes_and_preserves_private_mode(self):
        payload = secret_payload()
        agent_env.write_candidate(self.candidate, payload)
        self.assertEqual(self.candidate.read_bytes(), agent_env.build_environment(payload))
        self.assertEqual(stat.S_IMODE(self.candidate.stat().st_mode), 0o600)
        self.assertEqual(self.candidate.stat().st_nlink, 1)

    def test_existing_nonempty_file_is_never_overwritten(self):
        original = b'ORIGINAL=untouched\n'
        self.candidate.write_bytes(original)
        self.assert_private_rejection(self.candidate)
        self.assertEqual(self.candidate.read_bytes(), original)

    def test_invalid_payload_is_rejected_before_existing_destination_is_changed(self):
        original = b'ORIGINAL=untouched\n'
        self.candidate.write_bytes(original)
        self.assert_private_rejection(self.candidate, json.dumps({'MONGODB_URL': SECRET}))
        self.assertEqual(self.candidate.read_bytes(), original)

    def test_invalid_payload_is_rejected_before_any_destination_is_opened(self):
        with patch.object(agent_env.os, 'open') as opening:
            self.assert_private_rejection(self.candidate, '{' + SECRET)
        opening.assert_not_called()

    def test_short_writes_are_completed_without_losing_bytes(self):
        original_write = os.write
        calls = []
        def short_write(descriptor, content):
            calls.append(len(content))
            return original_write(descriptor, content[:3])
        with patch.object(agent_env.os, 'write', side_effect=short_write):
            agent_env.write_candidate(self.candidate, secret_payload())
        self.assertGreater(len(calls), 1)
        self.assertEqual(self.candidate.read_bytes(), agent_env.build_environment(secret_payload()))

    def test_write_sync_and_zero_write_failures_never_echo_raw_io_errors(self):
        for operation in ('write', 'fsync'):
            self.candidate.write_bytes(b'')
            with self.subTest(operation=operation), patch.object(agent_env.os, operation, side_effect=OSError(SECRET)):
                self.assert_private_rejection(self.candidate)
        self.candidate.write_bytes(b'')
        with patch.object(agent_env.os, 'write', return_value=0):
            self.assert_private_rejection(self.candidate)

    def test_main_reports_io_failure_as_only_the_fixed_code(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(agent_env.os.environ, {TRANSPORT: secret_payload()}, clear=True), \
                patch.object(agent_env.os, 'write', side_effect=OSError(SECRET)), \
                patch.object(agent_env.sys, 'stdout', stdout), patch.object(agent_env.sys, 'stderr', stderr):
            self.assertEqual(agent_env.main([str(self.candidate)]), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'Agent environment rejected: candidate_io_failed\n')

    def test_close_failure_is_sanitized_and_does_not_leak_a_descriptor(self):
        original_close = os.close
        def close_then_fail(descriptor):
            original_close(descriptor)
            raise OSError(SECRET)
        with patch.object(agent_env.os, 'close', side_effect=close_then_fail):
            self.assert_private_rejection(self.candidate)

    def test_missing_path_is_not_created(self):
        missing = self.root / ('missing-' + SECRET)
        self.assert_private_rejection(missing)
        self.assertFalse(missing.exists())

    def test_symlink_is_rejected_without_modifying_its_target(self):
        original = self.candidate.read_bytes()
        link = self.root / 'candidate-link.env'
        link.symlink_to(self.candidate)
        self.assert_private_rejection(link)
        self.assertEqual(self.candidate.read_bytes(), original)

    def test_directory_is_rejected(self):
        self.assert_private_rejection(self.root)

    def test_nonprivate_or_nonwritable_modes_are_rejected(self):
        for mode in (0o644, 0o640, 0o620, 0o400):
            with self.subTest(mode=oct(mode)):
                self.candidate.chmod(mode)
                self.assert_private_rejection(self.candidate)

    def test_foreign_owner_is_rejected(self):
        with patch.object(agent_env.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assert_private_rejection(self.candidate)

    def test_multiple_hardlinks_are_rejected(self):
        linked = self.root / 'other-link.env'
        os.link(self.candidate, linked)
        self.assert_private_rejection(self.candidate)
        self.assertEqual(linked.read_bytes(), b'')

    def test_oversized_existing_candidate_is_not_truncated(self):
        self.candidate.write_bytes(b'x' * (agent_env.MAX_ENV_BYTES + 1))
        size = self.candidate.stat().st_size
        self.assert_private_rejection(self.candidate)
        self.assertEqual(self.candidate.stat().st_size, size)

    def test_cli_refuses_fifo_without_blocking(self):
        fifo = self.root / 'fifo.env'
        os.mkfifo(fifo, 0o600)
        result = self.invoke_cli(destination=fifo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertNotIn(SECRET, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_cli_success_outputs_no_environment_values(self):
        result = self.invoke_cli()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(self.candidate.read_bytes(), agent_env.build_environment(secret_payload()))

    def test_cli_failure_exposes_only_a_fixed_code(self):
        values = valid_env()
        values['MONGODB_URL'] = SECRET + '\nHIDDEN=not-a-record'
        result = self.invoke_cli(secret_payload(values))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertRegex(result.stderr, r'\AAgent environment rejected: [A-Za-z0-9_-]+\n\Z')
        self.assertNotIn(SECRET, result.stderr)
        self.assertEqual(self.candidate.read_bytes(), b'')

    def test_missing_transport_has_no_traceback_or_destination_leak(self):
        private_path = self.root / ('private-' + SECRET)
        result = self.invoke_cli(None, private_path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertRegex(result.stderr, r'\AAgent environment rejected: [A-Za-z0-9_-]+\n\Z')
        self.assertNotIn(SECRET, result.stderr)
        self.assertFalse(private_path.exists())


class AgentWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text()
        marker = '      - name: Deploy with prefixed GitHub Secrets\n'
        if cls.source.count(marker) != 1:
            raise AssertionError('Expected exactly one prefixed-Secrets deployment step')
        cls.step = cls.source.split(marker, 1)[1]
        header, body = cls.step.split('        run: |\n', 1)
        if '      - name:' in body:
            raise AssertionError('The audited deployment step must remain the last workflow step')
        cls.header = header
        cls.shell = textwrap.dedent(body)

    def invoke(self, values=None, controller_exit=0, start_xtrace=False, transport=DEFAULT_PAYLOAD, extra=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'runner temp'
            runner.mkdir()
            log = root / 'controller.json'
            marker = root / 'shell-expansion-must-not-run'
            controller = root / 'fake-controller.py'
            controller.write_text('import json, os, pathlib, stat, sys\n'
                'args=sys.argv[1:]\n'
                'candidate=pathlib.Path(args[args.index("--env-file")+1])\n'
                'observed={"args":args,"path":str(candidate),"mode":stat.S_IMODE(candidate.stat().st_mode),'
                '"content":candidate.read_text(),"transportInherited":' + repr(TRANSPORT) + ' in os.environ,'
                '"appKeysInherited":[k for k in ' + repr(list(agent_env.REQUIRED_KEYS)) + ' if k in os.environ]}\n'
                'pathlib.Path(' + repr(str(log)) + ').write_text(json.dumps(observed))\n'
                'sys.exit(' + str(controller_exit) + ')\n')
            script = self.shell.replace('/home/kwung/deploy/bluegreen/bluegreen.py', shlex.quote(str(controller)))
            script = script.replace('scripts/bluegreen/agent_env.py', shlex.quote(str(SCRIPT)))
            environment = {'PATH': str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                'RUNNER_TEMP': str(runner), 'IMAGE_NAME': 'ieum-agent-dev', 'IMAGE_TAG': 'synthetic-sha',
                'IEUM_BLUEGREEN_JOB_DEADLINE_EPOCH': str(int(time.time()) + 21600)}
            if values is None:
                values = valid_env()
            elif callable(values):
                values = values(marker)
            if transport is DEFAULT_PAYLOAD:
                transport = secret_payload(values, extra)
            if transport is not None:
                environment[TRANSPORT] = transport
            command = ['bash'] + (['-x'] if start_xtrace else []) + ['-c', script]
            result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, timeout=5)
            observed = json.loads(log.read_text()) if log.exists() else None
            return result, observed, list(runner.iterdir()), marker.exists()

    def test_prefixed_transport_is_step_scoped_and_no_secret_expression_enters_shell(self):
        expected = TRANSPORT + ': ${{ toJSON(secrets) }}'
        self.assertIn(expected, self.header)
        self.assertEqual(self.source.count('${{ toJSON(secrets) }}'), 1)
        self.assertNotIn('toJSON(secrets)', self.source.split('      - name: Deploy with prefixed GitHub Secrets\n', 1)[0])
        self.assertNotIn('${{ secrets.', self.step)
        self.assertNotIn('${{', self.shell)
        self.assertNotIn('/home/kwung/deploy/ieum-agent/.env', self.shell)
        self.assertIn('unset ' + TRANSPORT, self.shell)
        self.assertLess(self.shell.index('scripts/bluegreen/agent_env.py'), self.shell.index('unset ' + TRANSPORT))
        self.assertLess(self.shell.index('unset ' + TRANSPORT), self.shell.index('/home/kwung/deploy/bluegreen/bluegreen.py'))

    def test_success_forwards_private_full_candidate_and_cleans_it_after_deploy(self):
        values = valid_env()
        values.update({key: 'synthetic-' + key.lower() for key in agent_env.OPTIONAL_KEYS})
        result, observed, remaining, _ = self.invoke(values)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(records(observed['content'].encode()), values)
        self.assertEqual(observed['mode'], 0o600)
        self.assertFalse(observed['transportInherited'])
        self.assertEqual(observed['appKeysInherited'], [])
        self.assertIn('--replace-env', observed['args'])
        self.assertEqual(observed['args'][observed['args'].index('--service') + 1], 'agent')
        self.assertEqual(observed['args'][observed['args'].index('--image') + 1], 'ieum-agent-dev:synthetic-sha')
        self.assertIn('--job-deadline-epoch', observed['args'])
        self.assertEqual(remaining, [])
        self.assertNotIn(SECRET, ' '.join(observed['args']))

    def test_new_prefixed_secret_is_used_without_workflow_mapping_changes(self):
        values = valid_env()
        values['NEW_SERVICE_TOKEN'] = SECRET
        result, observed, remaining, _ = self.invoke(values)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(records(observed['content'].encode())['NEW_SERVICE_TOKEN'], SECRET)
        self.assertEqual(remaining, [])

    def test_unprefixed_other_credentials_never_enter_candidate(self):
        extra = {'GITHUB_TOKEN': SECRET, 'AWS_SECRET_ACCESS_KEY': SECRET, 'OTHER_SERVICE_TOKEN': SECRET, 'MAIN_MONGODB_URL': SECRET,
                 'DEVELOPMENT_TOKEN': SECRET, 'DEVX_TOKEN': SECRET}
        result, observed, remaining, _ = self.invoke(extra=extra)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(records(observed['content'].encode()), valid_env())
        self.assertFalse(observed['transportInherited'])
        self.assertEqual(remaining, [])

    def test_controller_failure_preserves_status_and_cleans_temporary_candidate(self):
        result, observed, remaining, _ = self.invoke(controller_exit=23)
        self.assertEqual(result.returncode, 23)
        self.assertIsNotNone(observed)
        self.assertFalse(observed['transportInherited'])
        self.assertEqual(remaining, [])
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_missing_required_secret_prevents_controller_and_cleans_candidate(self):
        for key in agent_env.REQUIRED_KEYS:
            with self.subTest(key=key):
                values = valid_env()
                del values[key]
                result, observed, remaining, _ = self.invoke(values)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(observed)
                self.assertEqual(remaining, [])
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, 'Agent environment rejected: missing_required_configuration\n')

    def test_invalid_transport_prevents_controller_and_cleans_candidate(self):
        for invalid in (None, '', '{' + SECRET, '[]', secret_payload(extra={PREFIX + 'TINI_KILL_PROCESS_GROUP': SECRET})):
            with self.subTest(kind='invalid transport'):
                result, observed, remaining, _ = self.invoke(transport=invalid)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(observed)
                self.assertEqual(remaining, [])
                self.assertEqual(result.stdout, '')
                self.assertRegex(result.stderr, r'\AAgent environment rejected: [A-Za-z0-9_-]+\n\Z')
                self.assertNotIn(SECRET, result.stderr)

    def test_invalid_selected_value_prevents_controller_and_cleans_candidate(self):
        for invalid in (SECRET + '\rhidden', SECRET + '\nHIDDEN=extra', SECRET + '\u2028HIDDEN=extra',
                        SECRET + 'x' * agent_env.MAX_ENV_BYTES):
            with self.subTest(kind='invalid value'):
                values = valid_env()
                values['NOTION_MCP_AUTH_TOKEN'] = invalid
                result, observed, remaining, _ = self.invoke(values)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(observed)
                self.assertEqual(remaining, [])
                self.assertEqual(result.stdout, '')
                self.assertRegex(result.stderr, r'\AAgent environment rejected: [A-Za-z0-9_-]+\n\Z')
                self.assertNotIn(SECRET, result.stderr)

    def test_values_with_shell_syntax_are_not_executed_or_expanded(self):
        def values(marker):
            output = valid_env()
            output['NEW_SERVICE_TOKEN'] = SECRET + '$(touch ' + shlex.quote(str(marker)) + ')`touch ' + shlex.quote(str(marker)) + '`'
            return output
        result, observed, remaining, expanded = self.invoke(values)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertIn('$(touch ', records(observed['content'].encode())['NEW_SERVICE_TOKEN'])
        self.assertIn('`touch ', records(observed['content'].encode())['NEW_SERVICE_TOKEN'])
        self.assertFalse(expanded)
        self.assertFalse(observed['transportInherited'])
        self.assertEqual(remaining, [])

    def test_each_new_run_removes_deleted_dynamic_and_empty_optional_values(self):
        values = valid_env()
        values.update({'NOTION_MCP_AUTH_TOKEN': SECRET, 'NEW_SERVICE_TOKEN': SECRET})
        _, first, _, _ = self.invoke(values)
        del values['NOTION_MCP_AUTH_TOKEN']
        del values['NEW_SERVICE_TOKEN']
        result, second, remaining, _ = self.invoke(values)
        self.assertEqual(result.returncode, 0)
        self.assertIn('NOTION_MCP_AUTH_TOKEN', records(first['content'].encode()))
        self.assertIn('NEW_SERVICE_TOKEN', records(first['content'].encode()))
        self.assertNotIn('NOTION_MCP_AUTH_TOKEN', records(second['content'].encode()))
        self.assertNotIn('NEW_SERVICE_TOKEN', records(second['content'].encode()))
        self.assertIn('--replace-env', second['args'])
        self.assertEqual(remaining, [])

    def test_xtrace_is_disabled_before_transport_processing(self):
        result, observed, remaining, _ = self.invoke(start_xtrace=True)
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(observed)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stderr.strip(), '+ set +x')
        self.assertEqual(remaining, [])

    def test_tests_budget_and_release_pin_checks_precede_secret_deploy_step(self):
        deploy = self.source.index('      - name: Deploy with prefixed GitHub Secrets\n')
        for marker in ('      - name: Establish deployment job budget\n', '      - name: Test\n',
                       '      - name: Build Docker image\n', '      - name: Verify installed blue-green release\n'):
            self.assertLess(self.source.index(marker), deploy)
        self.assertIn("python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'", self.source)
        self.assertIn('--lock-timeout 14400 deploy', self.shell)
        self.assertIn('--job-deadline-epoch "$IEUM_BLUEGREEN_JOB_DEADLINE_EPOCH"', self.shell)
        self.assertNotIn('IEUM_BLUEGREEN_JOB_DEADLINE_EPOCH=', self.shell)


    def test_ci_uses_prefixed_test_database_secrets_without_deploying_them(self):
        self.assertIn('APP_MONGODB_URL: ${{ secrets.DEV_AGENT_MONGODB_URL || secrets.DEV_MONGODB_URL }}',self.source)
        self.assertIn('TEST_MONGODB_URL: ${{ secrets.DEV_AGENT_TEST_MONGODB_URL || secrets.DEV_TEST_MONGODB_URL }}',self.source)
        self.assertIn('TEST_MONGODB_DB_NAME: ${{ secrets.DEV_AGENT_TEST_MONGODB_DB_NAME || secrets.DEV_TEST_MONGODB_DB_NAME }}',self.source)
        self.assertIn('export MONGODB_URL="${TEST_MONGODB_URL:-$APP_MONGODB_URL}"',self.source)
        self.assertIn('export MONGODB_DB_NAME="${TEST_MONGODB_DB_NAME:-ieum_test}"',self.source)
        self.assertIn('pytest tests/',self.source)
        self.assertNotIn('secrets.AGENT_',self.source)

    def test_image_cannot_bake_a_stale_dotenv_source(self):
        ignore=(REPOSITORY/'.dockerignore').read_text().splitlines()
        for pattern in ('.env','.env.*','**/.env','**/.env.*'):
            self.assertIn(pattern,ignore)
        self.assertIn('COPY . .',(REPOSITORY/'Dockerfile').read_text())

    def test_ci_shell_preserves_test_url_precedence_and_test_database_fallback(self):
        step=self.source.split('      - name: Test\n',1)[1].split('      - name:',1)[0]
        script=textwrap.dedent(step.split('        run: |\n',1)[1])
        script=script.replace("python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'",'true')
        scenarios=(
            ({'APP_MONGODB_URL':'app-'+SECRET,'TEST_MONGODB_URL':'test-'+SECRET,
              'TEST_MONGODB_DB_NAME':'synthetic_test_db'},'test-'+SECRET,'synthetic_test_db'),
            ({'APP_MONGODB_URL':'app-'+SECRET,'TEST_MONGODB_URL':'',
              'TEST_MONGODB_DB_NAME':''},'app-'+SECRET,'ieum_test'),
        )
        for inputs,url,database in scenarios:
            with self.subTest(kind='synthetic CI fallback'), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                log=root/'pytest.json'
                fake=root/'pytest'
                fake.write_text('#!'+sys.executable+'\nimport json,os,pathlib\n'
                    'pathlib.Path('+repr(str(log))+').write_text(json.dumps({'
                    '"url":os.environ["MONGODB_URL"],"database":os.environ["MONGODB_DB_NAME"]}))\n')
                fake.chmod(0o755)
                environment={'PATH':str(root)+os.pathsep+os.defpath,**inputs}
                result=subprocess.run(['bash','-x','-c',script],env=environment,cwd=root,
                    text=True,capture_output=True,timeout=5)
                self.assertEqual(result.returncode,0)
                self.assertEqual(json.loads(log.read_text()),{'url':url,'database':database})
                self.assertNotIn(SECRET,result.stdout+result.stderr)
                self.assertEqual(result.stderr.strip(),'+ set +x')


if __name__ == '__main__':
    unittest.main()
