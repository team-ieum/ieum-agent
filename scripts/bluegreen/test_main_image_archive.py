"""Docker 저장 방식 간 동일 이미지 전달과 변조 거절. 합성 tar만 사용합니다."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

APP = 'agent'
REFERENCE = 'ieum-' + APP + '-main:' + 'a' * 40
LAYER = b'synthetic-layer'
CONFIG = {'architecture': 'amd64', 'os': 'linux', 'config': {'Env': ['APP_ENV=main']},
          'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(LAYER).hexdigest()]}}

spec = importlib.util.spec_from_file_location('archive_test_' + APP + '_image', Path(__file__).with_name('main_image.py'))
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


def write_archive(path, entries, links=()):
    with tarfile.open(path, 'w') as archive:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for name, kind, target in links:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = target
            archive.addfile(member)


def classic_archive_entries(reference, configuration=None):
    config = json.dumps(CONFIG if configuration is None else configuration).encode()
    config_id = 'sha256:' + hashlib.sha256(config).hexdigest()
    name = config_id[7:] + '.json'
    manifest = [{'Config': name, 'RepoTags': [reference], 'Layers': ['layer.tar']}]
    return config_id, {'manifest.json': json.dumps(manifest).encode(), name: config, 'layer.tar': LAYER}


def oci_archive_entries(reference, index=True, configuration=None):
    entries = {}

    def blob(value, media_type):
        content = value if isinstance(value, bytes) else json.dumps(value).encode()
        digest = 'sha256:' + hashlib.sha256(content).hexdigest()
        entries['blobs/sha256/' + digest[7:]] = content
        return {'mediaType': media_type, 'digest': digest, 'size': len(content)}

    config_type = 'application/vnd.oci.image.config.v1+json'
    manifest_type = 'application/vnd.oci.image.manifest.v1+json'
    config = blob(CONFIG if configuration is None else configuration, config_type)
    layer = blob(LAYER, 'application/vnd.oci.image.layer.v1.tar')
    executable = blob({'schemaVersion': 2, 'mediaType': manifest_type, 'config': config,
                       'layers': [layer]}, manifest_type)
    executable['platform'] = {'architecture': 'amd64', 'os': 'linux'}
    root = executable
    ids = {'config_id': config['digest'], 'executable_id': executable['digest']}
    if index:
        attestation_config = blob({'architecture': 'unknown', 'os': 'unknown'}, config_type)
        attestation = blob({'schemaVersion': 2, 'mediaType': manifest_type,
                            'config': attestation_config, 'layers': []}, manifest_type)
        attestation.update({'platform': {'architecture': 'unknown', 'os': 'unknown'},
            'annotations': {'vnd.docker.reference.type': 'attestation-manifest',
                            'vnd.docker.reference.digest': executable['digest']}})
        index_type = 'application/vnd.oci.image.index.v1+json'
        root = blob({'schemaVersion': 2, 'mediaType': index_type,
                     'manifests': [executable, attestation]}, index_type)
        ids.update(attestation_id=attestation['digest'], attestation_config_id=attestation_config['digest'])
    root['annotations'] = {'io.containerd.image.name': 'docker.io/library/' + reference,
                           'org.opencontainers.image.ref.name': reference.rsplit(':', 1)[1]}
    entries['oci-layout'] = b'{"imageLayoutVersion":"1.0.0"}'
    entries['index.json'] = json.dumps({'schemaVersion': 2, 'manifests': [root]}).encode()
    entries['manifest.json'] = json.dumps([{'Config': 'blobs/sha256/' + config['digest'][7:],
        'RepoTags': [reference], 'Layers': ['blobs/sha256/' + layer['digest'][7:]]}]).encode()
    ids['image_id'] = root['digest']
    return ids, entries


class MainImageArchiveTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.root.chmod(0o700)
        self.archive = self.root / 'image.tar'
        self.output = self.root / 'output'
        self.output.write_text('')
        self.environment = patch.dict(os.environ, {'RUNNER_TEMP': str(self.root), 'GITHUB_OUTPUT': str(self.output)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def digest(self):
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_classic_and_oci_manifest_or_index_identify_the_same_config(self):
        classic_id, entries = classic_archive_entries(REFERENCE)
        write_archive(self.archive, entries)
        self.assertEqual(image.validate_archive(self.archive, REFERENCE, classic_id), classic_id)
        for index in (False, True):
            with self.subTest(index=index):
                ids, entries = oci_archive_entries(REFERENCE, index=index)
                write_archive(self.archive, entries)
                self.assertNotEqual(ids['image_id'], classic_id)
                self.assertEqual(image.validate_archive(self.archive, REFERENCE, ids['image_id']), classic_id)

    def test_docker_hub_name_aliases_still_refer_to_one_main_image(self):
        classic_id, classic = classic_archive_entries(REFERENCE)
        ids, oci = oci_archive_entries(REFERENCE)
        for reference in ('library/' + REFERENCE, 'docker.io/library/' + REFERENCE):
            for source_id, entries in ((classic_id, classic), (ids['image_id'], oci)):
                with self.subTest(reference=reference, source_id=source_id):
                    write_archive(self.archive, entries)
                    self.assertEqual(image.validate_archive(self.archive, reference, source_id), classic_id)

    def test_packaging_verifies_both_archive_formats_and_keeps_source_engine_id(self):
        classic_id, classic = classic_archive_entries(REFERENCE)
        ids, oci = oci_archive_entries(REFERENCE)
        for source_id, entries in ((classic_id, classic), (ids['image_id'], oci)):
            with self.subTest(source_id=source_id):
                self.archive.unlink(missing_ok=True)
                def save(arguments):
                    self.assertEqual(arguments, ['docker', 'image', 'save', '--output', str(self.archive), REFERENCE])
                    write_archive(self.archive, entries)
                    return ''
                with patch.object(image, 'run', side_effect=save), patch.object(image, 'image_id', return_value=source_id):
                    result = image.package_image(self.archive, REFERENCE)
                self.assertEqual(result, {'image_id': source_id, 'archive_sha256': self.digest()})

    def test_loading_classic_or_containerd_on_same_store_keeps_immutable_id(self):
        classic_id, classic = classic_archive_entries(REFERENCE)
        ids, oci = oci_archive_entries(REFERENCE)
        for source_id, entries in ((classic_id, classic), (ids['image_id'], oci)):
            with self.subTest(source_id=source_id):
                write_archive(self.archive, entries)
                with patch.object(image, 'run', return_value='') as docker, \
                        patch.object(image, 'image_id', return_value=source_id):
                    self.assertEqual(image.load_image(self.archive, REFERENCE, source_id, self.digest()), source_id)
                docker.assert_called_once_with(['docker', 'image', 'load', '--input', str(self.archive)])

    def test_containerd_image_loaded_into_classic_returns_actual_config_id(self):
        ids, entries = oci_archive_entries(REFERENCE)
        write_archive(self.archive, entries)
        with patch.object(image, 'run', return_value='') as docker, \
                patch.object(image, 'image_id', return_value=ids['config_id']):
            loaded = image.load_image(self.archive, REFERENCE, ids['image_id'], self.digest())
        self.assertEqual(loaded, ids['config_id'])
        docker.assert_called_once_with(['docker', 'image', 'load', '--input', str(self.archive)])

    def test_classic_loaded_into_containerd_resaves_and_checks_config_before_returning_new_id(self):
        source_id, source = classic_archive_entries(REFERENCE)
        new_ids, resaved = oci_archive_entries(REFERENCE, index=False)
        write_archive(self.archive, source)
        before = set(self.root.iterdir())
        def docker(arguments):
            if arguments[:3] == ['docker', 'image', 'load']:
                return ''
            self.assertEqual(arguments[:4], ['docker', 'image', 'save', '--output'])
            self.assertEqual(arguments[5], REFERENCE)
            write_archive(Path(arguments[4]), resaved)
            return ''
        with patch.object(image, 'run', side_effect=docker), patch.object(image, 'image_id', return_value=new_ids['image_id']):
            loaded = image.load_image(self.archive, REFERENCE, source_id, self.digest())
        self.assertEqual(loaded, new_ids['image_id'])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_resaved_different_config_is_rejected_and_temporary_archive_is_removed(self):
        source_id, source = classic_archive_entries(REFERENCE)
        bad_config = {**CONFIG, 'config': {'Env': ['APP_ENV=wrong']}}
        wrong_ids, wrong = oci_archive_entries(REFERENCE, configuration=bad_config)
        write_archive(self.archive, source)
        before = set(self.root.iterdir())
        def docker(arguments):
            if arguments[:3] == ['docker', 'image', 'save']:
                write_archive(Path(arguments[4]), wrong)
            return ''
        with patch.object(image, 'run', side_effect=docker), patch.object(image, 'image_id', return_value=wrong_ids['image_id']), \
                self.assertRaises(ValueError):
            image.load_image(self.archive, REFERENCE, source_id, self.digest())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_cli_outputs_loaded_id_instead_of_ci_id_only_after_validation(self):
        ids, entries = oci_archive_entries(REFERENCE)
        write_archive(self.archive, entries)
        with patch.object(image, 'run', return_value=''), patch.object(image, 'image_id', return_value=ids['config_id']):
            result = image.main(['load', '--file', str(self.archive), '--image', REFERENCE,
                                 '--image-id', ids['image_id'], '--sha256', self.digest()])
        self.assertEqual(result, 0)
        self.assertEqual(self.output.read_text(), 'image_id=' + ids['config_id'] + '\n')

    def test_matching_archive_hash_still_cannot_import_a_dev_tag(self):
        config_id, entries = classic_archive_entries('ieum-' + APP + '-dev:' + 'a' * 40)
        write_archive(self.archive, entries)
        with patch.object(image, 'run') as docker, self.assertRaises(ValueError):
            image.load_image(self.archive, REFERENCE, config_id, self.digest())
        docker.assert_not_called()

    def test_wrong_or_extra_oci_tags_are_rejected_even_with_matching_docker_manifest(self):
        for mutation in ('name', 'ref', 'extra_root'):
            with self.subTest(mutation=mutation):
                ids, entries = oci_archive_entries(REFERENCE)
                root = json.loads(entries['index.json'])
                if mutation == 'name':
                    root['manifests'][0]['annotations']['io.containerd.image.name'] = 'ieum-' + APP + '-dev:latest'
                elif mutation == 'ref':
                    root['manifests'][0]['annotations']['org.opencontainers.image.ref.name'] = 'dev'
                else:
                    root['manifests'].append(root['manifests'][0])
                entries['index.json'] = json.dumps(root).encode()
                write_archive(self.archive, entries)
                with self.assertRaises(ValueError):
                    image.validate_archive(self.archive, REFERENCE, ids['image_id'])

    def test_corrupt_config_descriptor_size_digest_or_unrelated_identity_is_rejected(self):
        for mutation in ('config', 'descriptor_size', 'descriptor_digest', 'unrelated_id', 'attestation_id'):
            with self.subTest(mutation=mutation):
                ids, entries = oci_archive_entries(REFERENCE)
                expected = ids['image_id']
                if mutation == 'config':
                    entries['blobs/sha256/' + ids['config_id'][7:]] += b' '
                elif mutation == 'descriptor_size':
                    root = json.loads(entries['index.json'])
                    root['manifests'][0]['size'] += 1
                    entries['index.json'] = json.dumps(root).encode()
                elif mutation == 'descriptor_digest':
                    entries['blobs/sha256/' + ids['image_id'][7:]] += b' '
                elif mutation == 'unrelated_id':
                    expected = 'sha256:' + 'b' * 64
                else:
                    expected = ids['attestation_id']
                write_archive(self.archive, entries)
                with self.assertRaises(ValueError):
                    image.validate_archive(self.archive, REFERENCE, expected)

    def test_wrong_platform_or_conflicting_docker_compatibility_config_is_rejected(self):
        for mutation in ('platform', 'compatibility_config'):
            with self.subTest(mutation=mutation):
                config = {**CONFIG, 'architecture': 'arm64'} if mutation == 'platform' else \
                    {**CONFIG, 'config': {'Env': ['APP_ENV=wrong']}}
                ids, entries = oci_archive_entries(REFERENCE, configuration=config)
                if mutation == 'compatibility_config':
                    classic_id, classic = classic_archive_entries(REFERENCE)
                    name = 'blobs/sha256/' + classic_id[7:]
                    entries[name] = classic[classic_id[7:] + '.json']
                    manifest = json.loads(entries['manifest.json'])
                    manifest[0]['Config'] = name
                    entries['manifest.json'] = json.dumps(manifest).encode()
                write_archive(self.archive, entries)
                with self.assertRaises(ValueError):
                    image.validate_archive(self.archive, REFERENCE, ids['image_id'])

    def test_export_may_omit_attestation_but_not_executable_or_config_blobs(self):
        for omitted in ('attestation_id', 'attestation_config_id', 'executable_id', 'config_id'):
            with self.subTest(omitted=omitted):
                ids, entries = oci_archive_entries(REFERENCE)
                del entries['blobs/sha256/' + ids[omitted][7:]]
                write_archive(self.archive, entries)
                if omitted.startswith('attestation'):
                    self.assertEqual(image.validate_archive(self.archive, REFERENCE, ids['image_id']), ids['config_id'])
                else:
                    with self.assertRaises(ValueError):
                        image.validate_archive(self.archive, REFERENCE, ids['image_id'])

    def test_normalized_path_aliases_and_links_cannot_override_checked_members(self):
        for alias in ('./index.json', 'index.json/', 'blobs/../index.json', './manifest.json', 'blobs//sha256/unused'):
            with self.subTest(alias=alias):
                ids, entries = oci_archive_entries(REFERENCE)
                entries[alias] = entries['index.json']
                write_archive(self.archive, entries)
                with self.assertRaises(ValueError):
                    image.validate_archive(self.archive, REFERENCE, ids['image_id'])
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                ids, entries = oci_archive_entries(REFERENCE)
                write_archive(self.archive, entries, [('linked-index', kind, 'index.json')])
                with self.assertRaises(ValueError):
                    image.validate_archive(self.archive, REFERENCE, ids['image_id'])

    def test_archive_size_bound_is_checked_before_docker_load(self):
        config_id, entries = classic_archive_entries(REFERENCE)
        write_archive(self.archive, entries)
        with patch.object(image, 'MAX_ARCHIVE_BYTES', self.archive.stat().st_size - 1), \
                patch.object(image, 'run') as docker, self.assertRaises(ValueError):
            image.load_image(self.archive, REFERENCE, config_id, self.digest())
        docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
