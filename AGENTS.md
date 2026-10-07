# Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
for new commits and pull request titles:

```text
<type>[optional scope][optional !]: <description>
```

- Use `feat` for new features and `fix` for bug fixes.
- Use `docs`, `test`, `refactor`, `perf`, `build`, `ci`, or `chore` when appropriate.
- Add a scope when it helps identify the affected area, such as `browser` or `ui`.
- Write a concise, imperative description in English.
- For breaking changes, add `!` before the colon or a `BREAKING CHANGE:` footer
  explaining the change and migration.
- Keep any required attribution trailers at the end of the commit message.

Examples:

```text
feat(browser): use headless playback with explicit YouTube login
fix(ui): reset autoplay status after stopping
docs: document conventional commit guidelines
```
