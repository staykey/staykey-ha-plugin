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
