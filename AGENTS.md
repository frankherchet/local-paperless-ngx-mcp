# Repository contribution instructions

- Document every user-visible behavior, MCP interface, safety rule, compatibility change,
  configuration change, and operational change in `CHANGELOG.md` under `Unreleased` before
  committing or opening a pull request.
- Write commit subjects that state the intent of the change. Add a commit body when the subject
  alone does not explain notable behavior, safety, compatibility, or migration details.
- Pull request descriptions must summarize user-visible changes, safety implications,
  compatibility or breaking changes, and the verification performed. Keep that substance aligned
  with the changelog; avoid vague entries such as "miscellaneous fixes."
- For a release, move the relevant `Unreleased` entries into a versioned section with the release
  date and leave an empty `Unreleased` section for subsequent work.
- Never include credentials, private Paperless URLs, document contents, or other sensitive data in
  commits, pull requests, release notes, or the changelog.
