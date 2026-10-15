# Repository instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the application.

- Keep documentation in the same change as a feature or fix. Follow the
  [documentation standard](docs/development/documentation.md) and update the
  canonical page instead of adding a dated handoff or duplicate setup guide.
- Update [docs/STATUS.md](docs/STATUS.md) when behavior, acceptance evidence or
  known limits change. Preserve the distinction between implemented, automated,
  simulator, hardware and deployment evidence.
- After frontend implementation changes, run `make docs-screenshots`, inspect
  the current screenshots and commit them with their manifest. Replace/remove
  obsolete documented native media. Keep old recordings in the history section.
- Run `make docs-check` and appropriate application checks before finishing.
  Report a specific reason if a change has no documentation impact.
- Preserve dated release evidence. Normal test runs must write generated reports
  to ignored test output, not overwrite historical evidence.
