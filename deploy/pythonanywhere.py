"""Deploy a verified CI archive using only the official PythonAnywhere API.

No console execution, public webhook, database writes, or dependency installation.
API response bodies and authentication headers must never be logged.
"""

import argparse
import json
import os
import re
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_FILES = (
    'campusfind/__init__.py', 'campusfind/admin.py', 'campusfind/auth.py',
    'campusfind/claims.py', 'campusfind/db.py', 'campusfind/forms.py',
    'campusfind/items.py', 'campusfind/schema.sql', 'campusfind/static/style.css',
    'campusfind/templates/admin/dashboard.html',
    'campusfind/templates/auth/login.html', 'campusfind/templates/auth/register.html',
    'campusfind/templates/base.html', 'campusfind/templates/claims/mine.html',
    'campusfind/templates/errors/400.html', 'campusfind/templates/errors/403.html',
    'campusfind/templates/errors/404.html', 'campusfind/templates/errors/409.html',
    'campusfind/templates/errors/500.html', 'campusfind/templates/errors/503.html',
    'campusfind/templates/index.html', 'campusfind/templates/items/detail.html',
    'campusfind/templates/items/form.html', 'campusfind/templates/items/list.html',
    'campusfind/templates/items/mine.html', 'requirements.txt',
)
SHA = re.compile(r'[0-9a-f]{40}')
HOME = '/home/ZainMalik'
INITIAL_SOURCE = HOME + '/CampusFind'
RELEASES = HOME + '/campusfind-releases'
VENV = INITIAL_SOURCE + '/.venv'
WSGI = '/var/www/zainmalik_pythonanywhere_com_wsgi.py'
ENV_FILE = HOME + '/.config/campusfind.env'
DATABASE = HOME + '/campusfind-data/campusfind.sqlite'
BACKUPS = HOME + '/.config/campusfind-deploy'


class DeploymentError(Exception):
    """An intentionally sanitized operational failure."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API credential to a redirected destination.


def verify_archive(path, sha):
    if not SHA.fullmatch(sha):
        raise DeploymentError('Invalid tested commit SHA')
    expected = set(SOURCE_FILES) | {'RELEASE_SHA'}
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        if (len(members) != len(expected) or {m.name for m in members} != expected
                or any(not m.isfile() or m.size > 2_000_000 for m in members)):
            raise DeploymentError('Archive differs from the regular-file allowlist')
        files = {m.name: archive.extractfile(m).read() for m in members}
    if files['RELEASE_SHA'] != (sha + '\n').encode('ascii'):
        raise DeploymentError('Artifact release metadata does not match tested SHA')
    for name in SOURCE_FILES:
        committed = subprocess.check_output(['git', 'show', f'{sha}:{name}'])
        if files[name] != committed:
            raise DeploymentError('Artifact source differs from tested Git commit')
    return files


def wsgi_source(root, sha):
    """Only paths and a public commit ID are embedded; secrets stay on the host."""
    if root != INITIAL_SOURCE and not re.fullmatch(re.escape(RELEASES) + r'/[0-9a-f]{40}', root):
        raise DeploymentError('Unexpected release source path')
    if not SHA.fullmatch(sha):
        raise DeploymentError('Invalid release SHA')
    return f'''# Managed CampusFind loader; environment values remain outside source.
import os
import shlex
import sys
from pathlib import Path

values = {{}}
for line in Path({ENV_FILE!r}).read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if line.startswith('export '):
        line = line[7:]
    key, separator, raw = line.partition('=')
    if separator and key.strip() in {{'APP_ENV', 'SECRET_KEY', 'DATABASE', 'RELEASE_SHA'}}:
        parts = shlex.split(raw, comments=True)
        if len(parts) != 1:
            raise RuntimeError('Invalid protected environment file format')
        values[key.strip()] = parts[0]
if values.get('APP_ENV') != 'production' or values.get('DATABASE') != {DATABASE!r}:
    raise RuntimeError('Production environment or persistent database path is invalid')
os.environ.update(values)
os.environ['RELEASE_SHA'] = {sha!r}
os.environ.pop('FLASK_DEBUG', None)
sys.path.insert(0, {root!r})
from campusfind import create_app
application = create_app()
if application.debug or not application.config['SESSION_COOKIE_SECURE']:
    raise RuntimeError('Production security configuration is invalid')
# Drop temporary references; never print environment values.
del values, line, key, raw, parts
'''.encode()


class API:
    def __init__(self, username, host, domain, token):
        # Pin the authorized account and API origin before attaching credentials.
        if (username, host, domain) != ('ZainMalik', 'www.pythonanywhere.com', 'zainmalik.pythonanywhere.com'):
            raise DeploymentError('Unexpected PythonAnywhere account, host, or domain')
        if not token:
            raise DeploymentError('PYTHONANYWHERE_API_TOKEN is not configured')
        self.base = f'https://{host}/api/v0/user/{username}'
        self.web = f'/webapps/{domain}/'
        self.token = token
        self.opener = build_opener(NoRedirect())
        self.last_request = 0.0

    def request(self, method, path, data=None, content_type=None):
        # Files API is limited to 40 requests/minute; space all calls conservatively.
        delay = 1.7 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        headers = {'Authorization': 'Token ' + self.token}
        if content_type:
            headers['Content-Type'] = content_type
        request = Request(self.base + path, method=method, data=data, headers=headers)
        self.last_request = time.monotonic()
        try:
            with self.opener.open(request, timeout=30) as response:
                if response.status not in {200, 201, 202, 204}:
                    raise DeploymentError('Unexpected PythonAnywhere API status')
                return response.read()
        except HTTPError as error:
            # Response bodies can contain sensitive configuration. Do not display them.
            raise DeploymentError(f'PythonAnywhere {method} failed: HTTP {error.code}') from None
        except (URLError, TimeoutError, OSError):
            raise DeploymentError(f'PythonAnywhere {method} connection failed') from None

    def get_file(self, path):
        return self.request('GET', '/files/path' + quote(path, safe='/'))

    def upload(self, path, content):
        boundary = 'campusfind-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="content"; filename="source"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        self.request('POST', '/files/path' + quote(path, safe='/'), body,
                     'multipart/form-data; boundary=' + boundary)
        if self.get_file(path) != content:
            raise DeploymentError('Uploaded file readback did not match')

    def json(self, path):
        return json.loads(self.request('GET', path))

    def patch(self, path, fields):
        self.request('PATCH', path, urlencode(fields).encode(), 'application/x-www-form-urlencoded')

    def reload(self):
        self.request('POST', self.web + 'reload/', b'')


def public_health(url):
    try:
        request = Request(url + '/health', headers={'Cache-Control': 'no-cache'})
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            if response.status != 200:
                return None
            body = json.loads(response.read())
            if body.get('status') == 'ok' and SHA.fullmatch(body.get('release', '')):
                return body['release']
    except (OSError, ValueError):
        pass
    return None


def wait_for_release(url, sha, attempts=12):
    for attempt in range(attempts):
        if public_health(url) == sha:
            return True
        if attempt + 1 < attempts:
            time.sleep(5)
    return False


def deploy(api, files, sha, url):
    previous_sha = public_health(url)
    if previous_sha is None:
        raise DeploymentError('Live health must identify the previous release before deployment')
    config = api.json(api.web)
    root = config.get('source_directory')
    if root != INITIAL_SOURCE and not re.fullmatch(re.escape(RELEASES) + r'/[0-9a-f]{40}', root or ''):
        raise DeploymentError('Unrecognized existing source directory; inspect manually')
    if config.get('virtualenv_path') != VENV or str(config.get('python_version')).replace('python', '').replace('.', '') != '312':
        raise DeploymentError('Existing Python 3.12 virtualenv configuration must be inspected')
    if not config.get('force_https'):
        api.patch(api.web, {'force_https': 'true'})
        api.reload()
        if not api.json(api.web).get('force_https') or not wait_for_release(url, previous_sha):
            raise DeploymentError('Force HTTPS activation could not be verified')
        print('Enabled Force HTTPS and verified the existing release after Reload.', flush=True)
    maps = api.json(api.web + 'static_files/')
    if len(maps) != 1 or maps[0].get('url') != '/static/' or maps[0].get('path') != root + '/campusfind/static':
        raise DeploymentError('Unexpected static mappings; inspect them before deployment')
    mapping = maps[0]
    map_endpoint = api.web + f'static_files/{mapping["id"]}/'
    original_wsgi = api.get_file(WSGI)
    # The old source stays intact. Verify it really is the previously reported commit.
    for name in SOURCE_FILES:
        expected = subprocess.check_output(['git', 'show', f'{previous_sha}:{name}'])
        if api.get_file(root + '/' + name) != expected:
            raise DeploymentError('Previous source differs from its reported tested commit; retain and review manually')
    if api.get_file(root + '/requirements.txt') != files['requirements.txt']:
        raise DeploymentError('Dependency changes require a separately prepared Python 3.12 virtualenv')
    if previous_sha == sha:
        print('Selected release is already active and source-verified.')
        return
    release = RELEASES + '/' + sha
    print('Preflight passed; previous tested source will remain untouched.', flush=True)
    for name, content in files.items():
        api.upload(release + '/' + name, content)
    print('All 27 allowlisted release files uploaded and read back successfully.', flush=True)
    # Durable recovery loader contains no secrets; the exact original loader stays
    # in memory for immediate rollback. Neither operation reads or uploads the env file.
    api.upload(BACKUPS + '/previous-wsgi.py', wsgi_source(root, previous_sha))
    api.upload(BACKUPS + '/previous-release.json', json.dumps({
        'release': previous_sha, 'source_directory': root,
        'static_path': mapping['path'], 'wsgi_path': WSGI,
    }, sort_keys=True).encode())
    try:
        api.upload(WSGI, wsgi_source(release, sha))
        api.patch(api.web, {'source_directory': release})
        api.patch(map_endpoint, {'path': release + '/campusfind/static'})
        api.reload()
        if not wait_for_release(url, sha):
            raise DeploymentError('New release did not pass bounded HTTPS health checks')
    except Exception:
        print('Activation failed; restoring the previous loader and source/static configuration.', flush=True)
        try:
            api.upload(WSGI, original_wsgi)
            api.patch(api.web, {'source_directory': root})
            api.patch(map_endpoint, {'path': mapping['path']})
            api.reload()
            recovered = wait_for_release(url, previous_sha)
        except Exception:
            recovered = False
        if recovered:
            raise DeploymentError('Deployment failed; previous release restored and HTTPS health verified') from None
        raise DeploymentError('Deployment failed and rollback health is unconfirmed; manual recovery required') from None
    print('Deployment succeeded; HTTPS health matches tested SHA ' + sha, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('sha')
    args = parser.parse_args()
    files = verify_archive(args.archive, args.sha)
    url = os.environ.get('APP_URL')
    if url != 'https://zainmalik.pythonanywhere.com':
        raise DeploymentError('Unexpected APP_URL')
    api = API(os.environ.get('PYTHONANYWHERE_USERNAME'), os.environ.get('PYTHONANYWHERE_HOST'),
              os.environ.get('PYTHONANYWHERE_DOMAIN'), os.environ.get('PYTHONANYWHERE_API_TOKEN'))
    deploy(api, files, args.sha, url)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Only explicitly sanitized operational messages may reach Actions logs.
        print(str(error) if isinstance(error, DeploymentError) else 'Deployment failed; inspect configuration privately.')
        raise SystemExit(1) from None
