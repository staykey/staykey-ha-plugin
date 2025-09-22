## [1.1.0](https://github.com/staykey/staykey-ha-plugin/compare/v1.0.2...v1.1.0) (2025-09-22)


### Features

* **staykey:** use per-instance webhook URL and allow editing\n\n- Ask only for webhook URL in config (no default)\n- Remove property_id and StayKey header; read URL from options\n- Allow editing URL via options flow\n- Update strings/translations ([5c68b08](https://github.com/staykey/staykey-ha-plugin/commit/5c68b08dc23ecea576647902302d34eb495bb3d4))


### Bug Fixes

* **readme:** correct JSON structure in payload example ([16e4f95](https://github.com/staykey/staykey-ha-plugin/commit/16e4f95d928b95590a153bf131459036140ca703))
* **staykey:** update webhook URL description for clarity ([eee1ef5](https://github.com/staykey/staykey-ha-plugin/commit/eee1ef5320df725ed7fdb61879430b7e815f6cfe))

## [1.0.2](https://github.com/staykey/staykey-ha-plugin/compare/v1.0.1...v1.0.2) (2025-09-11)


### Bug Fixes

* trigger release to test updates in homeassistant ([963629d](https://github.com/staykey/staykey-ha-plugin/commit/963629d860876964a0025736f327b11e5bbeb6fb))

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
