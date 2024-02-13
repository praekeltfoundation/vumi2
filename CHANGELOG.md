# Changelog

## [0.1.1.dev0] - UNRELEASED

### Added

### Fixed
- Fix connector startup error (#53, #54)

### Changed
- Drop support for Python 3.9 and 3.10, add 3.12 to CI (#55)

## [0.1.0] - 2023-04-05

### Added

- Models (#1)
- To address router (#2, #14, #17)
- Configuration (#4)
- CLI (#5, #6)
- Sentry integration (#7)
- Documentation (#8)
- HTTP RPC base transport (#10, #13)
- AAT USSD transport (#11)
- Docker build (#15)
- Healthcheck endpoint (#16)
- SMPP transport (#19, #20, #21, #23, #24, #25, #26)

### Fixed

- Docs build (#12)
- AAT USSD callback URL (#18)
- Improved coverage reporting (#36)
- Clean up AMQP resources after tests (#37)

### Changed

- Connector abstraction (#3)
- Concurrent message processing (#9)
- Update dependancies (#28, #29, #32)
- Refactor config handling (#33)
- Switch to ruff for linting (#34)
- Type-related cleanups (#35)
- Switch to tag and branch-based image tags (#38)
