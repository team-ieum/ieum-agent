#!/usr/bin/env python3
"""Package and verify a Main image without transporting application settings."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile

APP = 'agent'
MAX_ARCHIVE_BYTES = 8 * 1024 ** 3
OCI_INDEX_TYPES = ('application/vnd.oci.image.index.v1+json',
                   'application/vnd.docker.distribution.manifest.list.v2+json')
OCI_MANIFEST_TYPES = ('application/vnd.oci.image.manifest.v1+json',
                      'application/vnd.docker.distribution.manifest.v2+json')


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
    archive_metadata(path)
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


def fail(code):
    raise ValueError(code)


def content_id(content):
    return 'sha256:' + hashlib.sha256(content).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('duplicate_image_json_key')
        result[key] = value
    return result


def reference_names(reference):
    """Docker의 짧은 이름과 같은 이미지의 정규 registry 이름만 허용합니다."""
    name, revision = reference.rsplit(':', 1)
    if name.startswith('docker.io/'):
        name = name[len('docker.io/'):]
        if name.startswith('library/'):
            name = name[len('library/'):]
    prefix = name.split('/', 1)[0]
    if '/' in name and ('.' in prefix or prefix == 'localhost'):
        canonical = name
    else:
        canonical = 'docker.io/' + (name if '/' in name else 'library/' + name)
    names = {reference, name + ':' + revision, canonical + ':' + revision}
    if canonical.startswith('docker.io/'):
        familiar = canonical[len('docker.io/'):]
        if familiar.startswith('library/'):
            familiar = familiar[len('library/'):]
        names.add(familiar + ':' + revision)
    return names


def private_directory(path):
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        fail('private_artifact_directory_required')


def archive_metadata(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1 or \
            not 0 < metadata.st_size <= MAX_ARCHIVE_BYTES:
        fail('invalid_image_archive')


def validate_oci(read, members, image, config_id, config_size, layers):
    """containerd가 우선 사용하는 OCI 참조도 같은 MAIN config에 연결되는지 확인합니다."""
    def document(content):
        value = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            fail('invalid_image_archive')
        return value

    def descriptor_path(descriptor):
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get('digest'), str) or \
                re.fullmatch(r'sha256:[a-f0-9]{64}', descriptor['digest']) is None or \
                type(descriptor.get('size')) is not int or not 0 <= descriptor['size'] <= 1024 * 1024:
            fail('invalid_image_descriptor')
        return 'blobs/sha256/' + descriptor['digest'][7:]

    def blob(descriptor):
        content = read(descriptor_path(descriptor), 1024 * 1024)
        if len(content) != descriptor['size'] or not hmac.compare_digest(content_id(content), descriptor['digest']):
            fail('image_descriptor_mismatch')
        return document(content)

    if document(read('oci-layout', 1024)).get('imageLayoutVersion') != '1.0.0':
        fail('invalid_image_archive')
    index = document(read('index.json', 65536))
    roots = index.get('manifests')
    if index.get('schemaVersion') != 2 or not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict):
        fail('invalid_image_archive')
    root = roots[0]
    annotations = root.get('annotations', {})
    # Docker는 짧은 로컬 이름을 docker.io/library/ 아래의 정규 이름으로 저장합니다.
    names = reference_names(image)
    if not isinstance(annotations, dict) or annotations.get('io.containerd.image.name') not in names or \
            annotations.get('org.opencontainers.image.ref.name', image.rsplit(':', 1)[1]) not in names | {image.rsplit(':', 1)[1]}:
        fail('unexpected_image_tag')

    executable_ids = set()
    attestation_targets = []
    visited = set()

    def walk(descriptor, depth=0):
        if depth > 8 or len(visited) >= 128:
            fail('invalid_image_archive')
        path = descriptor_path(descriptor)
        digest = descriptor['digest']
        if digest in visited:
            fail('invalid_image_archive')
        visited.add(digest)
        media_type = descriptor.get('mediaType')
        platform = descriptor.get('platform', {})
        annotations = descriptor.get('annotations', {})
        if not isinstance(platform, dict) or not isinstance(annotations, dict):
            fail('invalid_image_descriptor')
        attestation = depth > 0 and media_type in OCI_MANIFEST_TYPES and \
            platform == {'architecture': 'unknown', 'os': 'unknown'} and \
            annotations.get('vnd.docker.reference.type') == 'attestation-manifest'
        if attestation:
            attestation_targets.append(annotations.get('vnd.docker.reference.digest'))
            # docker save는 플랫폼 필터 때문에 provenance blob 없이 원본 index만 보존할 수 있습니다.
            if path not in members:
                return
        node = blob(descriptor)
        if node.get('schemaVersion') != 2:
            fail('invalid_image_archive')
        if node.get('mediaType', media_type) != media_type:
            fail('invalid_image_descriptor')
        if media_type in OCI_INDEX_TYPES:
            children = node.get('manifests')
            if not isinstance(children, list) or not children:
                fail('invalid_image_archive')
            for child in children:
                walk(child, depth + 1)
            return
        if media_type not in OCI_MANIFEST_TYPES:
            fail('invalid_image_descriptor')
        if attestation and descriptor_path(node.get('config')) not in members:
            return
        configuration = blob(node.get('config'))
        # BuildKit의 provenance는 실행 이미지가 아니므로 실행용 ID로 승인하지 않습니다.
        if attestation and \
                configuration.get('architecture') == 'unknown' and configuration.get('os') == 'unknown':
            return
        if configuration.get('architecture') != 'amd64' or configuration.get('os') != 'linux' or \
                platform.get('architecture', 'amd64') != 'amd64' or platform.get('os', 'linux') != 'linux':
            fail('invalid_image_platform')
        if node['config']['digest'] != config_id or node['config']['size'] != config_size:
            fail('image_config_mismatch')
        descriptors = node.get('layers')
        if not isinstance(descriptors, list) or any(not isinstance(layer, dict) or
                re.fullmatch(r'sha256:[a-f0-9]{64}', str(layer.get('digest', ''))) is None for layer in descriptors) or \
                ['blobs/sha256/' + layer['digest'][7:] for layer in descriptors] != layers:
            fail('image_layers_mismatch')
        executable_ids.add(digest)

    walk(root)
    if len(executable_ids) != 1 or any(target not in executable_ids for target in attestation_targets):
        fail('invalid_image_archive')
    return {root['digest'], *executable_ids}


def validate_archive(path, image, image_id):
    """단일 MAIN 이미지의 태그·플랫폼·ID 연결을 검증하고 저장 방식과 무관한 config ID를 반환합니다."""
    archive_metadata(path)
    if re.fullmatch(r'sha256:[a-f0-9]{64}', image_id) is None:
        fail('invalid_artifact_image')
    with tarfile.open(path, mode='r:') as archive:
        members = {}
        for index, member in enumerate(archive):
            name = PurePosixPath(member.name)
            # containerd는 경로를 정규화하므로 ./index.json 같은 별칭도 중복으로 취급해야 합니다.
            if index > 100000 or member.size < 0 or name.is_absolute() or '..' in name.parts or str(name) in members or \
                    (member.name != str(name) and not (member.isdir() and member.name == str(name) + '/')) or \
                    not (member.isfile() or member.isdir()):
                fail('invalid_image_archive')
            members[str(name)] = member

        def read(name, limit):
            entry = members.get(name)
            if entry is None or not entry.isfile() or entry.size > limit:
                fail('invalid_image_archive')
            with archive.extractfile(entry) as source:
                return source.read(limit + 1)

        manifest = json.loads(read('manifest.json', 65536), object_pairs_hook=unique_object)
        if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
            fail('invalid_image_archive')
        entry = manifest[0]
        tags = entry.get('RepoTags')
        if not isinstance(tags, list) or len(tags) != 1 or tags[0] not in reference_names(image) or not isinstance(entry.get('Config'), str):
            fail('unexpected_image_tag')
        config = read(entry['Config'], 1024 * 1024)
        config_id = content_id(config)
        if entry['Config'] not in (config_id[7:] + '.json', 'blobs/sha256/' + config_id[7:]):
            fail('image_config_mismatch')
        configuration = json.loads(config, object_pairs_hook=unique_object)
        if not isinstance(configuration, dict) or configuration.get('os') != 'linux' or configuration.get('architecture') != 'amd64':
            fail('invalid_image_platform')
        layers = entry.get('Layers')
        if not isinstance(layers, list) or any(not isinstance(layer, str) or layer not in members or
                not members[layer].isfile() for layer in layers):
            fail('invalid_image_archive')
        valid_ids = {config_id}
        if 'index.json' in members or 'oci-layout' in members:
            valid_ids.update(validate_oci(read, members, image, config_id, len(config), layers))
        if image_id not in valid_ids:
            fail('image_config_mismatch')
        return config_id



def package_image(path, reference):
    validate_image(reference)
    if path.exists() or path.is_symlink():
        raise ValueError('image_archive_already_exists')
    private_directory(path.parent)
    expected = image_id(reference)
    run(['docker', 'image', 'save', '--output', str(path), reference])
    if image_id(reference) != expected:
        raise ValueError('image_changed_during_packaging')
    digest = file_digest(path)
    validate_archive(path, reference, expected)
    return {'image_id': expected, 'archive_sha256': digest}


def load_image(path, reference, expected, digest):
    validate_image(reference)
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('invalid_artifact_identity')
    private_directory(path.parent)
    # Docker가 읽기 전에 전송 해시와 MAIN 태그·플랫폼·config 연결을 검증합니다.
    if not hmac.compare_digest(file_digest(path), digest):
        raise ValueError('image_archive_digest_mismatch')
    config_id = validate_archive(path, reference, expected)
    run(['docker', 'image', 'load', '--input', str(path)])
    loaded = image_id(reference)
    if loaded not in (expected, config_id):
        # classic → containerd는 새 manifest ID를 생성할 수 있습니다. 실제 로드된
        # 이미지를 다시 저장해 config 동일성을 확인하고 임시 archive를 정리합니다.
        with tempfile.TemporaryDirectory(prefix='ieum-' + APP + '-main-loaded.', dir=path.parent) as temporary:
            restored = Path(temporary) / 'image.tar'
            run(['docker', 'image', 'save', '--output', str(restored), reference])
            file_digest(restored)
            restored_config = validate_archive(restored, reference, loaded)
            if not hmac.compare_digest(restored_config, config_id):
                raise ValueError('loaded_image_identity_mismatch')
    return loaded


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
            loaded = load_image(args.file, args.image, args.image_id, args.sha256)
            write_outputs({'image_id': loaded}, Path(os.environ['GITHUB_OUTPUT']))
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError):
        print('Main image validation failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
