#!/usr/bin/env python3
"""Package and verify a Main image without transporting application settings."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

APP = 'agent'


def validate_image(reference):
    name, separator, revision = reference.rpartition(':')
    if not separator or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('invalid_image_reference')
    if len(name) > 128 or not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*', name):
        raise ValueError('invalid_image_name')
    namespace = set(re.split(r'[._/-]', name))
    if 'main' not in namespace or 'dev' in namespace:
        raise ValueError('main_image_namespace_required')
    return name


def validate_build(values):
    name = values.get('IMAGE_NAME', '')
    validate_image(name + ':' + 'a' * 40)
    version = values.get('JAVA_VERSION' if APP == 'backend' else 'PYTHON_VERSION', '')
    pattern = r'21(?:\.[0-9]+){0,2}' if APP == 'backend' else r'3\.11(?:\.[0-9]+)?'
    if not re.fullmatch(pattern, version):
        raise ValueError('unsupported_build_runtime')
    return '21' if APP == 'backend' else '3.11'


def run(arguments):
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=900).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError('image_command_failed') from None


def file_digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('invalid_image_archive')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def image_id(reference):
    value = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference])
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', value):
        raise ValueError('invalid_image_identity')
    return value


def package_image(path, reference):
    validate_image(reference)
    if path.exists() or path.is_symlink():
        raise ValueError('image_archive_already_exists')
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError('private_artifact_directory_required')
    expected = image_id(reference)
    run(['docker', 'image', 'save', '--output', str(path), reference])
    if image_id(reference) != expected:
        raise ValueError('image_changed_during_packaging')
    return {'image_id': expected, 'archive_sha256': file_digest(path)}


def load_image(path, reference, expected, digest):
    validate_image(reference)
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('invalid_artifact_identity')
    # Hash before asking Docker to read the archive; never trust a mutable tag alone.
    if file_digest(path) != digest:
        raise ValueError('image_archive_digest_mismatch')
    run(['docker', 'image', 'load', '--input', str(path)])
    if image_id(reference) != expected:
        raise ValueError('loaded_image_identity_mismatch')


def write_outputs(values, destination):
    with destination.open('a', encoding='utf-8') as stream:
        for key, value in values.items():
            stream.write(key + '=' + value + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('validate-build')
    package = subparsers.add_parser('package')
    package.add_argument('--file', type=Path, required=True)
    package.add_argument('--image', required=True)
    load = subparsers.add_parser('load')
    load.add_argument('--file', type=Path, required=True)
    load.add_argument('--image', required=True)
    load.add_argument('--image-id', required=True)
    load.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate-build':
            major = validate_build(os.environ)
            write_outputs({'BUILD_RUNTIME': major}, Path(os.environ['GITHUB_ENV']))
        elif args.command == 'package':
            write_outputs(package_image(args.file, args.image), Path(os.environ['GITHUB_OUTPUT']))
        else:
            load_image(args.file, args.image, args.image_id, args.sha256)
    except (ValueError, OSError, KeyError):
        print('Main image validation failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
