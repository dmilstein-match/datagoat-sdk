# Contributing

Thank you for helping. This repository is published from Datagoat's main (private) repository on
each release, so:

- **Issues** are the fastest way in: a bug, a confusing answer, a missing example. Include the
  request (without your key), the response's `code` and `request_id` if there is one, and the SDK
  version.
- **Pull requests** are welcome. We review them here, port the change to the main repository, and
  it comes back with the next release; your commit is credited.
- **Tests** must pass: `cd python && python -m pytest -q` and `cd typescript && npm install && npm test`.
- **The rule we keep:** a client never computes, rounds or reorders a chance, a level, a reason or a
  direction. It sends what the caller stated and returns what the engine answered.
