"""Build-input isolation and artifact checks for a committed US close."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import tempfile


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ['git', '--no-replace-objects', '-c', 'core.hooksPath=' + os.devnull,
         '-C', str(root), *args], env=env, capture_output=True, check=True,
    ).stdout


def check_index(root):
    if _git(root, 'diff', '--cached', '--name-only', '-z', '--ignore-submodules=none', 'HEAD', '--'):
        raise RuntimeError('source index has staged changes')


def check_source(root, commit, owned):
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or _git(root, 'rev-parse', 'HEAD').strip() != commit.encode():
        raise RuntimeError('source HEAD differs from reviewed commit')
    if not owned or len(set(owned)) != len(owned):
        raise RuntimeError('invalid owned scope')
    for path in owned:
        if not re.fullmatch(r'public/data/[a-zA-Z0-9][a-zA-Z0-9/_.-]*\.json', path) or '..' in Path(path).parts:
            raise RuntimeError(f'invalid owned path: {path}')
    check_index(root)
    if _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none', '--', *owned):
        raise RuntimeError('source has ambiguous owned output changes')
    # The index can hide worktree edits via assume-unchanged or skip-worktree.
    for path in owned:
        tracked = _git(root, 'ls-tree', '-z', commit, '--', path)
        if not tracked.startswith((b'100644 blob ', b'100755 blob ')):
            raise RuntimeError(f'owned output is not a tracked regular file: {path}')
        verify_file(Path(root), path, _git(root, 'cat-file', 'blob', f'{commit}:{path}'), 'owned output')


@contextmanager
def committed_tree(root, commit, owned, *, scratch=None):
    root = Path(root).resolve()
    check_source(root, commit, owned)
    with tempfile.TemporaryDirectory(prefix='us-close-candidate-', dir=scratch) as tmp:
        parent = Path(tmp)
        tree = parent / 'tree'
        _git(parent, '-c', 'protocol.file.allow=always', 'clone', '--shared', '--no-checkout', '--quiet', '--', str(root), str(tree))
        _git(tree, 'remote', 'remove', 'origin')
        _git(tree, 'checkout', '--detach', '--quiet', commit)
        if _git(tree, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none'):
            raise RuntimeError('candidate checkout is not clean')
        check_source(root, commit, owned)
        yield tree


def verify_dist(tree, commit, owned):
    if _git(tree, 'rev-parse', 'HEAD').strip() != commit.encode():
        raise RuntimeError('candidate HEAD changed')
    if _git(tree, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none'):
        raise RuntimeError('candidate checkout changed during build')
    for path in owned:
        expected = _git(tree, 'cat-file', 'blob', f'{commit}:{path}')
        verify_file(tree, path, expected, 'worktree')
        verify_file(tree, Path('dist') / Path(path).relative_to('public'), expected, 'dist')


def verify_file(root, path, expected, label):
    local = root / path
    if (local.resolve() != local or not local.is_file() or local.read_bytes() != expected):
        raise RuntimeError(f'{label} bytes differ from commit: {path}')
