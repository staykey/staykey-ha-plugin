# Staykey for Home Assistant

A Home Assistant integration that connects your smart home devices to your [Staykey](https://getstaykey.com) account. Staykey can remotely manage locks, thermostats, covers, switches, and more — all through a secure, persistent connection from your Home Assistant instance.

Learn more at [getstaykey.com](https://getstaykey.com).

## Features

- **Gateway mode** — persistent WebSocket connection to Staykey for real-time, two-way device control and state streaming
- **Automatic device discovery** — Staykey discovers supported devices (locks, climate, covers, switches, lights, sensors) from your Home Assistant instance
- **Z-Wave lock support** — keypad code management, lock/unlock, and activity event forwarding
- **State streaming** — device state changes are forwarded to Staykey in real time
- **Legacy webhook mode** — one-way event forwarding via HTTP POST (still supported alongside gateway mode)
- **Offline buffering** — events are queued when the connection is temporarily unavailable and sent on reconnect

## Requirements

- Home Assistant 2024.6 or newer
- HACS installed
- A Staykey account with a gateway token (provided by Staykey)
- Z-Wave JS integration (if managing Z-Wave locks)

## Installation (HACS)

1. In Home Assistant, open **HACS > Integrations > menu (three dots) > Custom repositories**
2. Add this repository URL, select category **Integration**, and click **Add**
3. Search for **Staykey** in HACS and click **Install**
4. Restart Home Assistant
5. Go to **Settings > Devices & Services > Add Integration > Staykey**
6. Enter your **Gateway Token** (provided by Staykey)

**Manual install:** copy `custom_components/staykey` into your Home Assistant `config/custom_components` directory and restart.

## Configuration

During setup you provide your Staykey gateway token. After setup, you can adjust options from:

**Settings > Devices & Services > Staykey > Configure**

Available options:

- **Gateway Token** — your Staykey-provided authentication token
- **Gateway URL** — WebSocket endpoint (uses the default unless directed otherwise by Staykey)
- **Forward all notifications** — send all Z-Wave notifications, not just lock events
- **Legacy Webhook URL** — optional HTTP endpoint for one-way event forwarding
- **SSL verification** and **timeout** settings for the legacy webhook

## Supported Devices

| Type | Capabilities |
|------|-------------|
| Locks | Lock, unlock, user code management, activity events |
| Climate | Set temperature, set HVAC mode |
| Covers | Open, close, stop |
| Switches | Turn on, turn off |
| Lights | Turn on, turn off |
| Sensors | State reporting |

## How It Works

When configured with a gateway token, the integration maintains a persistent connection to Staykey. Through this connection:

- Staykey can send commands to your devices (e.g., lock a door, set a thermostat)
- Device state changes are streamed to Staykey in real time
- Lock activity events (keypad codes, manual operations) are forwarded automatically

If you also configure a legacy webhook URL, lock events will be sent via webhook only when the gateway connection is unavailable.

## Security & Privacy

- Communication uses an authenticated WebSocket over TLS
- Only device state and event data needed by Staykey is transmitted
- No passwords, personal data, or network details are shared
- The gateway token uniquely identifies your Home Assistant instance to your Staykey account

## Troubleshooting

- **Logs:** Settings > System > Logs > search "staykey"
- **Connection issues:** the integration reconnects automatically with backoff; check logs for connection status
- **Z-Wave devices not appearing:** ensure Z-Wave JS is set up and devices are interviewed

## Development

The integration code lives in `custom_components/staykey`.

## License

MIT

## Releases

This repository uses semantic versioning with automated releases:

1. Merge commits to `main` using [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, etc.)
2. The release workflow computes the next version, updates `CHANGELOG.md` and `manifest.json`, creates a GitHub release, and tags it

HACS detects new releases automatically from GitHub tags.
