# StayKey Home Assistant Plugin

A Home Assistant custom integration that forwards relevant events (initially Z-Wave user code events) from your local Home Assistant to the StayKey backend.

## Features

- Listens for Z-Wave user code events (e.g., keypad code used) and forwards them to the StayKey webhook endpoint
- Config flow UI to enter your StayKey Integration ID, backend URL, and a signing secret
- Secure webhook signing using HMAC-SHA256
- Options flow to tweak behavior without re-adding the integration
- Future: optional secure tunnel to avoid exposing Home Assistant publicly

## Installation (HACS)

Recommended install via HACS:

1. In Home Assistant, open HACS → Integrations → menu (⋮) → Custom repositories.
2. Add this repository URL, select category "Integration", and click Add.
3. In HACS, search for "StayKey" and click Install.
4. Restart Home Assistant.
5. Go to Settings → Devices & Services → Add Integration → "StayKey" and enter:
   - Integration ID: Your StayKey integration identifier (issued by StayKey)
   - Events endpoint URL (optional): Defaults to production; override for development.

Alternative (manual): copy `custom_components/staykey` into your HA `config/custom_components` directory and restart.

## Event Forwarding

The integration subscribes to Z-Wave JS notifications. When a keypad/user code is used, a JSON payload is POSTed to the configured StayKey events endpoint.

Payload example:

```
{
  "integration_id": "abc123",
  "event_type": "zwave_js_notification",
  "hass_event": {
    "origin": "LOCAL",
    "time_fired": "2025-01-01T12:00:00Z",
    "data": { "event_label": "Keypad unlock operation", "parameters": { "userId": 1 } }
  },
  "context": {
    "hass_instance": "http://homeassistant.local",
    "component": "staykey"
  }
}
```

Headers include `X-StayKey-Id: <integration_id>`.

## Security

- The Integration ID links the HA instance to a StayKey account. Treat it as an access identifier.
- For development without signing, ensure TLS and restrict endpoint visibility. Future versions may add signed requests.

## Development

The integration code lives in `custom_components/staykey`.

Planned improvements:

- Retry with exponential backoff on webhook failures
- Health diagnostics and debug logging
- Optional tunnel-based connectivity without public exposure
 
## License
 
MIT
