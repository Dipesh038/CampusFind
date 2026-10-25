"""Release safety checks with a simulated host; no production API credentials."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import pythonanywhere as cd


OLD = 'a' * 40
NEW = 'b' * 40
FILES = {name: b'tested source' for name in cd.SOURCE_FILES}
FILES['RELEASE_SHA'] = (NEW + '\n').encode()


class Host:
    def __init__(self, fail_upload=False):
        self.web = '/webapps/test/'
        self.events = []
        self.fail_upload = fail_upload

    def json(self, path):
        if path.endswith('static_files/'):
            return [{'id': 1, 'url': '/static/', 'path': cd.INITIAL_SOURCE + '/campusfind/static'}]
        return {'source_directory': cd.INITIAL_SOURCE, 'virtualenv_path': cd.VENV,
                'python_version': '3.12', 'force_https': True}

    def get_file(self, path):
        return b'original loader' if path == cd.WSGI else b'tested source'

    def upload(self, path, content):
        self.events.append(('upload', path, content))
        if self.fail_upload and path.startswith(cd.RELEASES):
            raise cd.DeploymentError('Simulated staging failure')

    def patch(self, path, fields):
        self.events.append(('patch', path, fields))

    def reload(self):
        self.events.append(('reload',))


class DeploymentTests(unittest.TestCase):
    def test_staging_failure_does_not_activate_or_reload(self):
        host = Host(fail_upload=True)
        with patch.object(cd, 'public_health', return_value=OLD), patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
            with self.assertRaisesRegex(cd.DeploymentError, 'staging failure'):
                cd.deploy(host, FILES, NEW, 'https://test.invalid')
        self.assertFalse(any(event[0] in {'patch', 'reload'} or event[1] == cd.WSGI for event in host.events))

    def test_all_uploads_finish_before_activation_and_database_is_never_touched(self):
        host = Host()
        with patch.object(cd, 'public_health', return_value=OLD), patch.object(cd, 'wait_for_release', return_value=True), patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
            cd.deploy(host, FILES, NEW, 'https://test.invalid')
        activation = next(i for i, event in enumerate(host.events) if event[0] == 'upload' and event[1] == cd.WSGI)
        source_uploads = [event for event in host.events[:activation] if event[0] == 'upload' and event[1].startswith(cd.RELEASES)]
        self.assertEqual(len(source_uploads), 27)
        self.assertEqual(sum(event[0] == 'reload' for event in host.events), 1)
        writes = [event[1] for event in host.events if event[0] == 'upload']
        self.assertNotIn(cd.DATABASE, writes)
        self.assertNotIn(cd.ENV_FILE, writes)
        self.assertFalse(any(path.startswith(cd.INITIAL_SOURCE + '/') for path in writes))

    def test_failed_new_health_restores_previous_loader_mapping_and_checks_old_release(self):
        host = Host()
        with patch.object(cd, 'public_health', return_value=OLD), patch.object(cd, 'wait_for_release', side_effect=[False, True]) as health, patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
            with self.assertRaisesRegex(cd.DeploymentError, 'previous release restored'):
                cd.deploy(host, FILES, NEW, 'https://test.invalid')
        self.assertEqual(health.call_args_list[-1].args[-1], OLD)
        self.assertIn(('upload', cd.WSGI, b'original loader'), host.events)
        self.assertIn(('patch', host.web, {'source_directory': cd.INITIAL_SOURCE}), host.events)
        self.assertEqual(sum(event[0] == 'reload' for event in host.events), 2)

    def test_failed_rollback_is_reported_as_unconfirmed(self):
        host = Host()
        with patch.object(cd, 'public_health', return_value=OLD), patch.object(cd, 'wait_for_release', return_value=False), patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
            with self.assertRaisesRegex(cd.DeploymentError, 'manual recovery required'):
                cd.deploy(host, FILES, NEW, 'https://test.invalid')

    def test_dependency_change_stops_before_any_write(self):
        host = Host()
        files = dict(FILES, **{'requirements.txt': b'changed dependencies'})
        with patch.object(cd, 'public_health', return_value=OLD), patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
            with self.assertRaisesRegex(cd.DeploymentError, 'Dependency changes'):
                cd.deploy(host, files, NEW, 'https://test.invalid')
        self.assertEqual(host.events, [])

    def test_archive_rejects_mismatch_extra_members_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'release.tar.gz'
            for mode in ('wrong-sha', 'extra', 'symlink'):
                with self.subTest(mode=mode):
                    with tarfile.open(path, 'w:gz') as archive:
                        for name, content in FILES.items():
                            if name == 'RELEASE_SHA' and mode == 'wrong-sha':
                                content = (OLD + '\n').encode()
                            info = tarfile.TarInfo(name)
                            if name == cd.SOURCE_FILES[0] and mode == 'symlink':
                                info.type = tarfile.SYMTYPE
                                info.linkname = '/etc/passwd'
                                archive.addfile(info)
                            else:
                                info.size = len(content)
                                archive.addfile(info, io.BytesIO(content))
                        if mode == 'extra':
                            archive.addfile(tarfile.TarInfo('campusfind.sqlite'))
                    with self.assertRaises(cd.DeploymentError):
                        cd.verify_archive(path, NEW)

    def test_successful_archive_requires_every_source_byte_match_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'release.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                for name, content in FILES.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            with patch.object(cd.subprocess, 'check_output', return_value=b'tested source'):
                self.assertEqual(cd.verify_archive(path, NEW), FILES)
            with patch.object(cd.subprocess, 'check_output', return_value=b'tampered source'):
                with self.assertRaisesRegex(cd.DeploymentError, 'source differs'):
                    cd.verify_archive(path, NEW)

    def test_credentials_cannot_be_sent_to_another_api_host(self):
        with self.assertRaisesRegex(cd.DeploymentError, 'Unexpected PythonAnywhere'):
            cd.API('ZainMalik', 'attacker.invalid', 'zainmalik.pythonanywhere.com', 'fake-token')
        self.assertIsNone(cd.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://attacker.invalid'))

    def test_generated_loader_validates_production_and_loads_metadata_without_embedded_secret(self):
        source = cd.wsgi_source(cd.RELEASES + '/' + NEW, NEW).decode()
        compile(source, '<generated WSGI>', 'exec')
        self.assertIn(cd.ENV_FILE, source)
        self.assertIn(cd.DATABASE, source)
        self.assertIn("os.environ['RELEASE_SHA'] = '" + NEW, source)
        with self.assertRaises(cd.DeploymentError):
            cd.wsgi_source('/unrelated/path', NEW)
