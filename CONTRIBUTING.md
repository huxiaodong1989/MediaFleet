# Contributing

Thank you for contributing to MediaFleet.

## Development workflow

1. Create a focused branch from the default branch.
2. Keep service-private behavior in its owning service and shared contracts in
   `media_platform`.
3. Add or update tests for message contracts, idempotency, state transitions,
   and routing changes.
4. Run the relevant tests and `git diff --check`.
5. Update documentation when public behavior or architecture changes.

Pull requests should describe the problem, chosen design, validation, and
operational or migration impact. Do not combine large directory moves with
unrelated behavior changes.

Do not submit secrets, private deployment data, proprietary assets, or model
weights without confirmed redistribution rights.
