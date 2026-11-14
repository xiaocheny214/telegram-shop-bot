# Contributing

Work moves through `Proposal → Issue → branch → pull request → merge`. Every change starts from an issue, and code reaches `master` only through a pull request. Read this page once, in order, before the first contribution.

## Fork and branches

People with write access open a branch in this repository. Everyone else forks `xiaocheny214/telegram-shop-bot` to a personal account, works there, and opens a pull request back here. The clone and sync commands below are only for a fork.

```bash
git clone git@github.com:<your-account>/telegram-shop-bot.git
cd telegram-shop-bot
git remote add upstream git@github.com:xiaocheny214/telegram-shop-bot.git
```

Sync with upstream before opening a branch:

```bash
git fetch upstream
git checkout master
git rebase upstream/master
git checkout -b <branch-name>
```

Name the branch `type/short-description`, for example `feat/wallet-top-up` or `fix/payment-amount`.

## Issues

- Choose a template. Blank issues are disabled. A large change starts as a product proposal; split it into tasks only after it is accepted.
- State the background, the goal, and the acceptance criteria. Do not use a vague title such as "improve X".
- The issue title starts with one of `[Bug]:`, `[Feature]:`, `[Docs]:`, `[Experiment]:`, or `[Proposal]:`, followed by a space.
- Assign an owner, and attach the issue to the current milestone. An issue without a milestone is not in the current plan.
- When closing an issue, say why: which pull request solved it, which issue replaces it, or that the team agreed it is no longer needed.

Proposal state is tracked with labels: `proposal` (draft) → `Proposal-Accepted` (accepted for development), `Proposal-Denied` (rejected), or `Proposal-NoPlan` (not scheduled). Add `FullSpec` to a large proposal and `MiniSpec` to a small one. Add `Need-Document` when the feature is done but the user documentation is missing, and replace it with `Documented` after the documentation is updated.

## Pull requests

- The title uses the same prefix as the linked issue, then a space. The allowed prefixes are `[Bug]:`, `[Feature]:`, `[Docs]:`, `[Experiment]:`, and `[Proposal]:`. A title with a different prefix fails the `PR Issue Link` check.
- Write `Closes #<issue>` in the description so the pull request is linked to the issue it delivers. A pull request with no linked issue fails the `PR Issue Link` check and cannot be merged.
- Keep the change inside the issue. Do not add unrelated work. Prefer small, frequent pull requests over one change of several thousand lines.
- Follow the pull request template: why the change exists, what changed, how it was implemented, how it was verified, and what is out of scope. If a check was not run, write "not run" and say why. Do not claim a test that was not run.
- Merge does not require a review, but nobody pushes straight to `master`. The author must be able to explain what the change does and why it is done this way.

## Commits

Commit messages are English and use `type(scope): subject`. The subject is an imperative sentence, starts with a lowercase letter, and has no final period. Common types are `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, and `build`. One commit does one thing. Keep dependency updates, tests, documentation, and product code in separate commits.

## Releases

At the end of each milestone, create a `vX.Y.Z` tag and publish a release. The release notes list what that milestone delivered. Deploy only a published version, never unmerged code.

## Local secrets

Keep API keys, bot tokens, and the payment private key in a local `.env` file. `.env` is listed in `.gitignore`. When a new variable is required, update `.env.example` with the name and a description only. Do not write a real value there.
