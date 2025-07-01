# Changelog

## [0.3.3-dev] - UNRELEASED
### Added
- Message cache for storing inbound messages
- Link last inbound message to outbound messages for USSD flows
- Retry messages if Turn rate limits us
- Warning logs for config errors, including (ignored) extra keys
- Log an error if the outbound request times out

### Fixed
- Sphinx builds now fail on warnings, and all existing warnings/errors in the docs have been fixed

## [0.3.2] - 2025-01-22
### Added
- Add Turn Channels application


## [0.3.1] - 2024-12-04

### Changed
- Updated dependencies (#82)
- Replaced black with ruff format and updated various linter configs (#82)
- Use attrs in the EsmeClient  (#85)

### Added

### Fixed
- added a stream publisher to ensure that one pdu is sent at a time (#83)

## [0.3.0] - 2024-10-29

### Changed
- BREAKING CHANGE: If the `VUMI_CONFIG_FILE` is unset, we no longer look for a default config file. If the envvar is set but the file can't be found, we now exit with an error instead of silently using an empty config (#78)

### Added

### Fixed
- Worker class loading now has better reporting for import-related errors, and no longer masks errors from within the module(s) being imported. (#77, #81)

## [0.2.2] - 2024-09-25

### Added

### Fixed
- Correctly parse message timestamps with a trailing Z UTC indicator (#74)

### Changed

## [0.2.1] - 2024-07-09 (with 0.2.1.dev0 in pyproject.toml)

### Added

### Fixed
- Decode potential delivery reports as latin1 to prevent encoding-related crashes (#71)

### Changed

## [0.2.0] - 2024-02-20

### Added

### Fixed

### Changed
- Limit toaddressrouter to one mapping match. This is a breaking change because of a new config format for the ToAddressRouter (#62)

## [0.1.2] - 2024-02-19

### Added
Add static reply application (#59, #61)

### Fixed

### Changed
- Add default app to ToAddressRouter (#60)

## [0.1.1] - 2024-02-13

### Added

### Fixed
- Fix connector startup error (#53, #54)

### Changed
- Drop support for Python 3.9 and 3.10 (#55)
- Update dependencies and add support for Python 3.12 (#56, #57)
- Switch to timezone-aware objects for message timestamps (#58)

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
