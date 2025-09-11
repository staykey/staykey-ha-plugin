## [1.0.1](https://github.com/staykey/staykey-ha-plugin/compare/v1.0.0...v1.0.1) (2025-09-11)


### Bug Fixes

* trigger release to test updates in homeassistant ([aea1b8e](https://github.com/staykey/staykey-ha-plugin/commit/aea1b8eb1a56aa50314bec0c4441bc6421f7a86f))

## 1.0.0 (2025-09-11)


### Bug Fixes

* update readme ([fc39c41](https://github.com/staykey/staykey-ha-plugin/commit/fc39c41a9dd1d898ea13cddbfcce75841ab72d39))
* update semver release workflow ([0dee7e9](https://github.com/staykey/staykey-ha-plugin/commit/0dee7e9477f84024d08f3497ddb07605a96cf2aa))
* update semver release workflow ([ecef917](https://github.com/staykey/staykey-ha-plugin/commit/ecef917694a4b966a7dbe357a3e5f829dea4f66e))
* update semver release workflow ([5194dd9](https://github.com/staykey/staykey-ha-plugin/commit/5194dd9f07cd1fcc300169e145fdc93c227d76eb))

# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2025-09-11
### Added
- Custom webhook schema with device/entity enrichment and plugin version
- Whitelist for Z-Wave JS Notification (CC 113, Type 6, events 1/2/6)
- Property ID configuration and `X-StayKey-Property-Id` header
- Default production endpoint https://staykey.co/orion/api/v1/webhooks/homeassistant

### Changed
- Event type now derived from HA `event_label` to snake_case

## [0.1.0] - 2025-09-11
### Added
- Initial release with basic event forwarding and config/option flows
