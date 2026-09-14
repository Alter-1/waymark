#!/usr/bin/env python3
"""Reusable evidence workflows. Standard library only; Python 3.7+; no shell strings."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(str(temporary), str(path))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def absolute(path, base):
    p = Path(path)
    return (p if p.is_absolute() else base / p).resolve()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo)] + list(args), stderr=subprocess.STDOUT).decode('utf-8', 'replace').strip()


def repo_state(repo):
    return {'path': str(repo), 'head': git(repo, 'rev-parse', 'HEAD'),
            'branch': git(repo, 'branch', '--show-current'),
            'status': git(repo, 'status', '--porcelain')}


def new_evidence(path):
    out = Path(path).resolve()
    out.mkdir(parents=True, exist_ok=False)
    return out


def run_plan(plan_path, out, resume=False):
    """Run ordered argv arrays. A receipt survives failures; resume requires identical inputs."""
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
    base = plan_path.parent
    out = Path(out).resolve()
    steps = plan['steps']
    names = [s['id'] for s in steps]
    if len(set(names)) != len(names) or any(not n or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in n) for n in names):
        raise ValueError('Step ids must be unique alphanumeric/underscore/hyphen names')
    for step in steps:
        if not isinstance(step['argv'], list) or not step['argv'] or not all(isinstance(a, str) for a in step['argv']):
            raise ValueError('argv must be a nonempty array of strings, never a shell command')
        if not isinstance(step.get('expect', 0), int):
            raise ValueError('expect must be an exact exit code (use a separate successful compile step)')
    inputs = {str(absolute(p, base)): digest(absolute(p, base)) for p in plan.get('inputs', [])}
    repos = [repo_state(absolute(r['path'], base)) for r in plan.get('repositories', [])]
    for spec, state in zip(plan.get('repositories', []), repos):
        if spec.get('branch') and spec['branch'] != state['branch']:
            raise ValueError('Unexpected branch: ' + state['path'])
    fingerprint = {'plan': digest(plan_path), 'inputs': inputs, 'repositories': repos}
    receipt_path = out / 'receipt.json'
    if resume:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['fingerprint'] != fingerprint:
            raise ValueError('Plan, inputs, or repository state changed; use a new evidence directory')
    else:
        new_evidence(out)
        shutil.copy2(str(plan_path), str(out / 'plan.json'))
        receipt = {'fingerprint': fingerprint, 'steps': [], 'complete': False}
    completed = {s['id'] for s in receipt['steps'] if s['passed']}
    write_json(receipt_path, receipt)
    for step in steps:
        if step['id'] in completed:
            continue
        cwd = absolute(step.get('cwd', '.'), base)
        # Only this explicit placeholder is expanded; no shell/env interpolation.
        argv = [a.replace('{evidence}', str(out)) for a in step['argv']]
        attempt = 1 + sum(s['id'] == step['id'] for s in receipt['steps'])
        log = out / ('%s-%d.log' % (step['id'], attempt))
        result = {'id': step['id'], 'argv': argv, 'cwd': str(cwd), 'log': log.name,
                  'started_utc': datetime.datetime.utcnow().isoformat() + 'Z', 'passed': False}
        with log.open('wb') as stream:
            try:
                code = subprocess.call(argv, cwd=str(cwd), stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=step.get('timeout_seconds', 600))
                result['exit_code'] = code
                result['passed'] = code == step.get('expect', 0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result['error'] = str(exc)
        receipt['steps'].append(result)
        write_json(receipt_path, receipt)
        print('%s: %s (%s)' % (step['id'], 'PASS' if result['passed'] else 'FAIL', log), flush=True)
        if not result['passed']:
            return 1
    receipt['complete'] = True
    write_json(receipt_path, receipt)
    return 0


def snapshot(config_path, out):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    out = new_evidence(out)
    report = {'utc': datetime.datetime.utcnow().isoformat() + 'Z', 'repositories': [], 'files': [], 'errors': []}
    for r in config.get('repositories', []):
        try:
            report['repositories'].append(repo_state(absolute(r, config_path.parent)))
        except (OSError, subprocess.CalledProcessError) as exc:
            report['errors'].append(str(exc))
    for index, spec in enumerate(config.get('files', [])):
        path = absolute(spec['path'], config_path.parent)
        try:
            stat = path.stat()
            item = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            mode = spec.get('mode', 'metadata')
            dest = out / ('%03d-%s' % (index, path.name))
            if mode == 'tail':
                with path.open('rb') as stream:
                    size = max(0, min(int(spec.get('bytes', 65536)), stat.st_size))
                    stream.seek(stat.st_size - size)
                    dest.write_bytes(stream.read(size))
                item['artifact'] = dest.name
            elif mode == 'copy':
                shutil.copy2(str(path), str(dest)); item['artifact'] = dest.name
                item['sha256'] = digest(dest)
            elif mode == 'hash':
                item['sha256'] = digest(path)
            elif mode != 'metadata':
                raise ValueError('Unknown snapshot mode: ' + mode)
            report['files'].append(item)
        except (OSError, ValueError) as exc:
            report['errors'].append('%s: %s' % (path, exc))
    write_json(out / 'snapshot.json', report)
    return 1 if report['errors'] else 0


def sparse_worktree(repo, dest, ref, branch, includes, excludes=()):
    repo, dest = Path(repo).resolve(), Path(dest).resolve()
    if dest.exists():
        raise ValueError('Destination already exists; never overwrite an existing worktree')
    def pattern(path):
        path = path.replace('\\', '/').strip('/')
        if not path or any(p in ('', '.', '..') for p in path.split('/')) or any(c in path for c in '*?[]!\n\r') or ':' in path:
            raise ValueError('Use literal repository-relative paths, not globs: ' + path)
        return '/' + path
    patterns = [pattern(p) + '/' for p in includes] + ['!' + pattern(p) + '/' for p in excludes]
    # --no-checkout is essential: do not unpack bundled environments before sparsity exists.
    git(repo, 'worktree', 'add', '--no-checkout', '-b', branch, str(dest), ref)
    try:
        # Worktree-local configuration also supports Git versions predating sparse-checkout.
        git(repo, 'config', 'extensions.worktreeConfig', 'true')
        git(dest, 'config', '--worktree', 'core.sparseCheckout', 'true')
        sparse_file = absolute(git(dest, 'rev-parse', '--git-path', 'info/sparse-checkout'), dest)
        sparse_file.parent.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text('\n'.join(patterns) + '\n', encoding='utf-8')
        git(dest, 'read-tree', '-mu', 'HEAD')
    except Exception:
        print('Partial worktree retained for inspection: ' + str(dest), file=sys.stderr)
        raise
    return repo_state(dest)


def compare(config_path, out):
    config_path = Path(config_path).resolve(); base = config_path.parent
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    report = {'repositories': {}, 'items': [], 'limitation': 'Byte comparisons are triage, not semantic port certification.'}
    repos = {key: absolute(path, base) for key, path in config['repositories'].items()}
    for key, repo in repos.items():
        report['repositories'][key] = repo_state(repo)
    for spec in config['items']:
        result = {'id': spec['id'], 'source': spec['source'], 'targets': {}}
        source_root = repos[spec['source']]
        for target, policy in spec['targets'].items():
            if policy.get('decision') == 'will-not-port':
                if not policy.get('reason'):
                    raise ValueError('will-not-port requires a reason')
                result['targets'][target] = dict(policy)
                continue
            files = []
            for relative in spec['files']:
                paths = [absolute(relative, root) for root in (source_root, repos[target])]
                for path, root in zip(paths, (source_root, repos[target])):
                    if root not in path.parents:
                        raise ValueError('Comparison path escapes repository')
                hashes = [digest(p) if p.is_file() else None for p in paths]
                files.append({'path': relative, 'source_sha256': hashes[0], 'target_sha256': hashes[1],
                              'result': 'identical' if hashes[0] is not None and hashes[0] == hashes[1] else 'review-required'})
            result['targets'][target] = {'files': files, 'decision': 'review-required', 'evidence': policy.get('evidence', '')}
        report['items'].append(result)
    out = new_evidence(out); write_json(out / 'comparison.json', report)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'snapshot', 'compare'):
        p = sub.add_parser(name); p.add_argument('config'); p.add_argument('--out', required=True)
        if name == 'run': p.add_argument('--resume', action='store_true')
    p = sub.add_parser('worktree'); p.add_argument('--repo', required=True); p.add_argument('--dest', required=True)
    p.add_argument('--ref', required=True); p.add_argument('--branch', required=True)
    p.add_argument('--include', action='append', required=True); p.add_argument('--exclude', action='append', default=[])
    args = parser.parse_args()
    try:
        if args.command == 'run': return run_plan(args.config, args.out, args.resume)
        if args.command == 'snapshot': return snapshot(args.config, args.out)
        if args.command == 'compare': return compare(args.config, args.out)
        print(json.dumps(sparse_worktree(args.repo, args.dest, args.ref, args.branch, args.include, args.exclude), indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr); return 2


if __name__ == '__main__':
    sys.exit(main())
