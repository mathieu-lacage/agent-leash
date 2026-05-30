# Release process

## How to create a new release

The release process is fully automated via GitHub Actions. The workflow is triggered by tagging a commit with a version tag (`v*`).

### Manual steps

1. Bump the version in `pyproject.toml`:
```console
$ uv version --bump minor
```

2. Commit the version bump:
```console
$ git add pyproject.toml
$ git commit -m "release: $(uv version --short)"
```

3. Create a version tag (must match the version in `pyproject.toml`):
```console
$ git tag v$(uv version --short)
```

4. Push to GitHub (triggers the release workflow):
```console
$ git push github main --tags
```

### What happens next (automated)

The `.github/workflows/release.yml` workflow automatically:

1. **Validates** the tag matches the version in `pyproject.toml`
5. **Builds** the Python distribution (wheel + sdist)
6. **Publishes to PyPI** (via OIDC Trusted Publisher, no token needed)
8. **Creates a GitHub Release** with both the Python dist and the squashfs artifact
