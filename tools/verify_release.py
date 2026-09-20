"""Offline release checks. No HA, credentials, network or device requests."""
import ast
import hashlib
import json
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    component = root / 'custom_components/docan_deye_ems'
    integrations = [p.name for p in (root / 'custom_components').iterdir() if p.is_dir() and p.name != '__pycache__']
    assert integrations == ['docan_deye_ems'], 'Exactly one public integration is permitted'
    manifest = json.loads((component / 'manifest.json').read_text(encoding='utf-8'))
    hacs = json.loads((root / 'hacs.json').read_text(encoding='utf-8'))
    expected_repo = 'https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems'
    assert manifest['documentation'] == expected_repo + '#readme'
    assert manifest['issue_tracker'] == expected_repo + '/issues'
    assert manifest['codeowners'] == ['@Xaiamasters']
    assert manifest['config_flow'] is True
    assert manifest['integration_type'] == 'hub'
    assert hacs['name'] == manifest['name'] == 'Docan Panda & Deye EMS'
    assert hacs['homeassistant'] == '2026.5.3'
    assert (component / 'brand/icon.png').is_file()

    tree = ast.parse((component / 'const.py').read_text(encoding='utf-8'))
    constants = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)}
    assert ast.literal_eval(constants['VERSION']) == manifest['version'] == '0.4.0-beta.1'
    platforms = constants['PLATFORMS']
    assert isinstance(platforms, ast.List)
    assert {node.attr for node in platforms.elts if isinstance(node, ast.Attribute)} == {'SENSOR', 'BINARY_SENSOR'}
    prohibited_files = {'services.yaml', 'services.yml', 'switch.py', 'button.py', 'number.py', 'select.py', 'climate.py', 'cover.py'}
    assert not any((component / name).exists() for name in prohibited_files)
    forbidden_calls = {'async_call', 'call_service', 'write_register', 'write_registers', 'write_coil', 'write_coils'}
    for source in component.glob('*.py'):
        module = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, 'Control call detected in ' + source.name
                receiver = node.func.value
                assert not (node.func.attr in {'register', 'async_register'} and
                            isinstance(receiver, ast.Attribute) and receiver.attr == 'services'), 'Service registration detected'

    front = component / 'frontend'
    inventory = json.loads((front / 'DEPENDENCY_INVENTORY.json').read_text(encoding='utf-8'))['current_files']
    for entry in inventory:
        path = (front / entry['path']).resolve()
        assert path.is_relative_to(front.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], 'Dependency changed: ' + entry['path']
    strings = json.loads((component / 'strings.json').read_text(encoding='utf-8'))
    assert strings == json.loads((component / 'translations/en.json').read_text(encoding='utf-8')), 'English translations differ from strings'
    assert 'experimental' in strings['config']['step']['user']['description'].lower()
    for name in ('README.md', 'CHANGELOG.md', 'SUPPORT.md', 'TESTING.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE'):
        assert (root / name).is_file(), 'Missing release document: ' + name
    # Broad secret detection and known-owner scans run separately before sealing.
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '.git' in relative.parts:
            continue
        assert not {'evidence', 'backups', 'private', '.venv'}.intersection(relative.parts), 'Private working directory entered the release'
        assert not path.name.startswith('PRIVATE_'), 'Private deployment artifact entered the release'
        assert path.name not in {'.env', 'secrets.yaml', '.storage'}, 'Private configuration entered the release'
        assert not path.name.lower().endswith(('.db', '.sqlite', '.pem', '.key')), 'Private-state file entered the release'
    print(json.dumps({'passed': True, 'version': manifest['version'], 'integrations': integrations,
                      'dependency_files_verified': len(inventory), 'read_only_static_contract': True,
                      'scope': 'Offline package checks; not remote HACS validation or physical hardware verification'}))


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, KeyError, OSError, ValueError) as error:
        print('Release verification failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
