# Repository setup checklist

Do these on github.com after the first push to `main`. Every item is free for
public repositories.

## Settings → General
- [ ] Features: Issues on; Wiki off (docs live in the repo); Projects optional.
- [ ] Pull Requests: allow squash merging only; "Automatically delete head branches" on.

## Settings → Rules → Rulesets → New branch ruleset
Name `protect-main`, enforcement **Active**, target the default branch:
- [ ] Restrict deletions
- [ ] Block force pushes
- [ ] Require signed commits
- [ ] Require status checks to pass (add the CI checks once CI exists in phase 0.1)

Direct pushes to `main` stay allowed so a solo workflow works; tighten to
"Require a pull request" later if contributors join.

## Settings → Code security
- [ ] Private vulnerability reporting: **Enable**
- [ ] Dependabot alerts: **Enable**
- [ ] Dependabot security updates: **Enable**
- [ ] Secret scanning: **Enable**, and **Push protection: Enable**
- [ ] Code scanning (CodeQL): enable "Default setup" once there is code (phase 0.1)

## Settings → Actions → General
- [ ] Workflow permissions: **Read repository contents** (workflows request
      extra permissions explicitly, e.g. `packages: write` for GHCR)
- [ ] "Allow GitHub Actions to create and approve pull requests": off

## After the first image is published (phase 0.1)
- [ ] Packages → `backuphaven` → Package settings: visibility **Public** and
      link it to this repository, so Unraid can pull without logging in.
