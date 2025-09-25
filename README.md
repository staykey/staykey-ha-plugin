# StayKey for Home Assistant

An easy way for StayKey customers to send lock activity from Home Assistant to StayKey. The integration listens for supported Z‑Wave lock events and securely forwards them to your StayKey account using a unique per‑instance webhook URL.

## Features

- Listens for Z-Wave user code events (e.g., keypad code used) and forwards them to StayKey via a per‑instance webhook
- Config flow UI to enter your StayKey Webhook URL
- Options flow to update the Webhook URL after setup

## Requirements

- Home Assistant (2024.6 or newer)
- HACS installed
- Z-Wave JS integration for your lock(s)
- Your StayKey Webhook URL (provided by StayKey for this HA instance)

## Installation (HACS)

Recommended install via HACS:

1. In Home Assistant, open HACS → Integrations → menu (⋮) → Custom repositories.
2. Add this repository URL, select category "Integration", and click Add.
3. In HACS, search for "StayKey" and click Install.
4. Restart Home Assistant.
5. Go to Settings → Devices & Services → Add Integration → "StayKey" and enter:
   - Webhook URL: the StayKey‑provided webhook URL for this Home Assistant instance

Alternative (manual): copy `custom_components/staykey` into your HA `config/custom_components` directory and restart.

## What data is sent?

When a supported lock event occurs, the integration POSTs a JSON payload to StayKey using this schema (example values shown):

Payload example:

```
{
  "schema_version": "1.0",
  "event_id": "3f6bb6f1-7ef3-4d4e-9d39-3f4d6a0c9e6b",
  "occurred_at": "2025-09-11T05:45:46.797026Z",
  "event_type": "keypad_unlock_operation",
  "device": {
    "device_id": "a1a3e9cf7416afea66faee1f60d3877d",
    "entity_id": "lock.front_door",
    "name": "Front Door Lock",
    "manufacturer": "Kwikset",
    "model": "914"
  },
  "access": {
    "method": "keypad",
    "code_slot": 251,
    "result": "success"
  },
  "plugin": { "version": "1.2.3", "instance_url": "https://home.example.com" },
  "ha": {
    "event_type": "zwave_js_notification",
    "event_label": "Keypad unlock operation",
    "node_id": 12,
    "command_class_name": "Notification"
  }
}
```

 

## Which events are sent?

Only specific Z‑Wave JS Notification events from the Access Control category are forwarded:

- Manual lock operation (command_class 113, type 6, event 1)
- Manual unlock operation (command_class 113, type 6, event 2)
- Keypad unlock operation (command_class 113, type 6, event 6)

## Security & privacy

- Your unique Webhook URL links this Home Assistant instance to the correct StayKey property.
- Payloads contain only lock event details and basic device metadata needed by StayKey.
- If you need to test locally, you can use an HTTP endpoint on your LAN. In production, keep the default HTTPS endpoint.

## Configuration

During initial setup you provide the StayKey Webhook URL. After setup, you can adjust options from:

Settings → Devices & Services → StayKey → Configure

Available options:

- Webhook URL: override or update the StayKey webhook URL after setup.

Tips:

- For local testing, you can use an HTTP endpoint on your LAN (e.g., `http://192.168.x.x:8000/...`).
- To see logs: Settings → System → Logs → search “StayKey”.

## Development

The integration code lives in `custom_components/staykey`.

Planned improvements:

- Retry with exponential backoff on webhook failures
- Health diagnostics and debug logging
- Optional tunnel-based connectivity without public exposure
 
## License
 
MIT

## Developer: Releases

This repository follows semantic versioning. To publish an update consumable by HACS:

Option A (automatic via GitHub Actions with Conventional Commits):

1. Merge commits to `main` using Conventional Commits (feat:, fix:, chore:, docs:, etc.)
2. The Release workflow will compute the next version, update `CHANGELOG.md` and `manifest.json`, create a GitHub release, and tag it.

Option B (manual):

1. Update `custom_components/staykey/manifest.json` version
2. Update `CHANGELOG.md`
3. Create a Git tag matching the version (e.g., `v0.2.0`)
4. Push the tag to the remote so HACS can detect the new release

