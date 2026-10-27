#!/usr/bin/env python3
"""Build a private deployment candidate from DEV_ Actions secrets.

This module never reads a server .env file, expands shell expressions, or prints
configuration values. The controller owns the final per-color runtime snapshots.
"""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


MAX_ENV_BYTES = 48 * 1024
MAX_JSON_BYTES = 96 * 1024
SECRET_PREFIX = 'DEV_'
SECRETS_VARIABLE = 'IEUM_AGENT_SECRETS_JSON'
FORBIDDEN_CHARACTERS = '\x00\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029'

# Preserve existing Agent requirements while accepting new prefixed app keys.
REQUIRED_KEYS = (
    'MONGODB_URL', 'MONGODB_DB_NAME', 'PORT', 'CLAUDE_DEFAULT_MODEL',
    'OPENAI_DEFAULT_MODEL', 'GEMINI_DEFAULT_MODEL',
)
DEFAULTS = {}
OPTIONAL_KEYS = ('NOTION_MCP_URL', 'NOTION_MCP_AUTH_TOKEN')
ALIASES = {
    'AGENT_MONGODB_URL': 'MONGODB_URL', 'AGENT_MONGODB_DB_NAME': 'MONGODB_DB_NAME',
    'AGENT_PORT': 'PORT', 'AGENT_CLAUDE_DEFAULT_MODEL': 'CLAUDE_DEFAULT_MODEL',
    'AGENT_OPENAI_DEFAULT_MODEL': 'OPENAI_DEFAULT_MODEL',
    'AGENT_GEMINI_DEFAULT_MODEL': 'GEMINI_DEFAULT_MODEL',
    'AGENT_NOTION_MCP_URL': 'NOTION_MCP_URL', 'AGENT_NOTION_MCP_AUTH_TOKEN': 'NOTION_MCP_AUTH_TOKEN',
}
# CI credentials and fixed build metadata must not become runtime app settings.
EXCLUDED_KEYS = frozenset((
    'AGENT_TEST_MONGODB_URL', 'AGENT_TEST_MONGODB_DB_NAME',
    'TEST_MONGODB_URL', 'TEST_MONGODB_DB_NAME', 'PYTHON_VERSION', 'IMAGE_NAME',
))
# The controller owns signal propagation. Python/shell bootstrap settings are not
# application options and could execute code or bypass the fixed launcher.
RESERVED_KEYS = (
    'TINI_KILL_PROCESS_GROUP', 'GITHUB_TOKEN', 'AGENT_ENV', SECRETS_VARIABLE,
    'PATH', 'PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONINSPECT', 'PYTHONWARNINGS',
    'PYTHONBREAKPOINT', 'BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS',
    'LD_PRELOAD', 'LD_LIBRARY_PATH',
)
RESERVED_PREFIXES = ('IEUM_DEPLOYMENT_',)


def canonical_key(key):
    return key.replace('_', '').upper()


RESERVED_CANONICAL_KEYS = frozenset(canonical_key(key) for key in RESERVED_KEYS)
RESERVED_CANONICAL_PREFIXES = tuple(canonical_key(prefix) for prefix in RESERVED_PREFIXES)
EXCLUDED_CANONICAL_KEYS = frozenset(canonical_key(key) for key in EXCLUDED_KEYS)


class AgentEnvironmentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AgentEnvironmentError('duplicate_secret_name')
        result[key] = value
    return result


def invalid_json_constant(_value):
    raise AgentEnvironmentError('invalid_json')


def build_environment(payload):
    """Select the prefix from JSON and rebuild a complete literal environment."""
    if not isinstance(payload, str) or not payload:
        raise AgentEnvironmentError('missing_configuration')
    try:
        encoded = payload.encode('utf-8')
    except UnicodeError:
        raise AgentEnvironmentError('invalid_encoding') from None
    if len(encoded) > MAX_JSON_BYTES:
        raise AgentEnvironmentError('secrets_payload_too_large')
    try:
        environment = json.loads(payload, object_pairs_hook=unique_object,
                                 parse_constant=invalid_json_constant)
    except (json.JSONDecodeError, RecursionError):
        raise AgentEnvironmentError('invalid_json') from None
    if not isinstance(environment, dict):
        raise AgentEnvironmentError('invalid_json')
    values = {}
    seen = set()
    for secret_name, value in environment.items():
        normalized_name = secret_name.upper()
        if not normalized_name.startswith(SECRET_PREFIX):
            continue
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', secret_name) is None:
            raise AgentEnvironmentError('invalid_secret_name')
        suffix = normalized_name[len(SECRET_PREFIX):]
        if re.fullmatch(r'[A-Z_][A-Z0-9_]*', suffix) is None:
            raise AgentEnvironmentError('invalid_secret_name')
        if canonical_key(suffix) in EXCLUDED_CANONICAL_KEYS:
            continue
        key = ALIASES.get(suffix, suffix)
        canonical = canonical_key(key)
        if canonical in RESERVED_CANONICAL_KEYS or canonical.startswith(RESERVED_CANONICAL_PREFIXES):
            raise AgentEnvironmentError('reserved_configuration')
        if canonical in seen:
            raise AgentEnvironmentError('duplicate_configuration_key')
        # Check collisions even when a known optional empty value is omitted.
        seen.add(canonical)
        if not isinstance(value, str):
            raise AgentEnvironmentError('invalid_configuration')
        if any(character in value for character in FORBIDDEN_CHARACTERS):
            raise AgentEnvironmentError('unsupported_control_character')
        values[key] = value
    for key in REQUIRED_KEYS:
        if not values.get(key) or values[key].isspace():
            raise AgentEnvironmentError('missing_required_configuration')
    # The installed controller and Docker health endpoint use port 8000.
    # A free-form PORT would also be word-split by the image's sh launcher.
    if values['PORT'] != '8000':
        raise AgentEnvironmentError('invalid_port')
    for key, default in DEFAULTS.items():
        if values.get(key, '') == '':
            values[key] = default
    for key in OPTIONAL_KEYS:
        if values.get(key) == '':
            values.pop(key)
    try:
        result = ''.join(key + '=' + values[key] + '\n' for key in sorted(values)).encode('utf-8')
    except UnicodeError:
        raise AgentEnvironmentError('invalid_encoding') from None
    if len(result) > MAX_ENV_BYTES:
        raise AgentEnvironmentError('configuration_too_large')
    return result


def write_candidate(destination, payload):
    """Populate only an empty, owned, private regular file created by mktemp."""
    content = build_environment(payload)
    descriptor = None
    try:
        descriptor = os.open(Path(destination), os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or \
                stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
            raise AgentEnvironmentError('unsafe_candidate')
        if metadata.st_size != 0:
            raise AgentEnvironmentError('candidate_not_empty')
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError()
            remaining = remaining[count:]
        os.fsync(descriptor)
    except AgentEnvironmentError:
        raise
    except (OSError, ValueError, TypeError):
        raise AgentEnvironmentError('candidate_io_failed') from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                raise AgentEnvironmentError('candidate_io_failed') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    arguments = parser.parse_args(argv)
    try:
        write_candidate(arguments.candidate, os.environ.get(SECRETS_VARIABLE))
    except AgentEnvironmentError as failure:
        print('Agent environment rejected: ' + failure.code, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
