# zipp-keepalive

Keeps a **Libratone Zipp**'s 3.5 mm **aux / line-in** jack from dropping out after
~20 seconds of near-silence — by nudging the speaker's power state over its own
Wi-Fi control protocol, independent of audio level. Quiet movie scenes, dialogue,
paused-but-not-silent games: the jack stays alive.

Libratone support confirms there is **no setting** to disable aux standby. This
works around it without touching the speaker's firmware.

> ⚠️ Not affiliated with or endorsed by Libratone. "Libratone" and "Zipp" are
> trademarks of their owner, used here only to describe compatibility. Use at
> your own risk.

## Is this legitimate?

Yes. It talks to **speakers you own**, on **your own network**, using the
speaker's **existing control protocol** — the same messages the official Libratone
app sends.

- **No firmware modification, no cracking, no password or encryption bypass** —
  the control protocol is unauthenticated by Libratone's design.
- No Libratone firmware, binaries, or other IP is included or redistributed.
- The protocol was reverse-engineered by the community (see Credits); this builds
  on that public work.

## What it does

- **Discovers** every Libratone on your LAN by a quick unicast sweep — no hardcoded
  IPs, finds multiple units, re-detects the subnet each pass so it follows a laptop
  between networks.
- **Power-aware**: reads each speaker's charging state. Plugged in → keeps it awake.
  On battery → leaves it to sleep so the battery is spared (override with
  `--nudge-on-battery`, with a `--battery-floor`).
- **Quiet hours**: optionally stop nudging overnight (`--quiet-hours 1-7`).
- Logs only on state changes, to a self-rotating log. Pure Python 3 standard
  library — **no dependencies**.

## Requirements

- **macOS** for the background service (it uses `launchd`). The daemon itself is
  plain Python 3 and works anywhere; on Linux/Windows run it manually or under your
  own service manager, and pass `--cidr` if subnet auto-detect (macOS-specific)
  doesn't apply.
- Python 3.
- Tested on a **Libratone Zipp 2, firmware v879**. Other Zipp models/firmwares may
  work — reports welcome.

## Quick check (changes nothing)

```
python3 zipp_keepalive.py --status
```
Lists every Libratone it can see — IP, name, power source, battery %, and what it
would do.

## Install as a background service (macOS, starts at login)

```
./install.sh                    # eco default: keep awake only when plugged in
./install.sh --nudge-on-battery # keep awake on battery too (respects --battery-floor, default 30%)
./install.sh --quiet-hours 1-7  # any daemon flag is passed straight through
```
`install.sh` writes a LaunchAgent with your paths filled in and loads it.

Uninstall:
```
./uninstall.sh
```

Confirm it's running (`PID` on the left = running):
```
launchctl list | grep zipp
```

## Run manually (testing)

```
python3 zipp_keepalive.py                       # eco: plugged-in only
python3 zipp_keepalive.py --nudge-on-battery
python3 zipp_keepalive.py --quiet-hours 0-8
python3 zipp_keepalive.py --cidr 192.168.1.0/24 # fix the subnet instead of auto-detecting
```
Press Ctrl-C to stop.

### Flags
| flag | meaning |
|------|---------|
| `--nudge-on-battery` | also keep speakers awake on battery (default: plugged-in only) |
| `--battery-floor PCT` | with `--nudge-on-battery`, stop below this % (default 30) |
| `--quiet-hours A-B` | skip nudging during these local hours, e.g. `0-8` or `23-6` |
| `--cidr CIDR` | fix the subnet to scan (default: auto-detect each sweep) |
| `--interval SECONDS` | seconds between nudges (default 10) |
| `--logfile PATH` | rotating log file (default: next to the script) |
| `--status` | print current LAN status and exit |

## How it works

- UDP control protocol: commands to port **7777**, replies on **7778**. 10-byte
  header: `AA AA | type(1) | cmd(2) | status(1) | crc(2) | datalen(2) | ascii-data`.
- Keep-awake nudge: `SET CurrPowerMode = 0` (active) + `Timer` wakeup `"00"`, every
  10 s (under the ~20 s standby window).
- Power source: `ChargingStatus` (cmd 1284) — `0` = on battery, non-zero = plugged in.
- Discovery is a unicast sweep because the speakers ignore broadcast and only answer
  unicast. (They also advertise over Bonjour/mDNS as `_raop._tcp`, if you prefer to
  discover that way.)
- Aux is **not** a selectable "source" on the Zipp 2 — it plays through line-in while
  still reporting the Wi-Fi source — which is why keep-awake is gated on *power
  source*, not on "is aux selected".

Values verified on a Zipp 2, firmware v879.

## Tested environment

- **Speakers:** Libratone Zipp 2, firmware **v879** (a stereo pair — two units,
  discovered independently).
- **Host:** macOS Sequoia (Darwin 24.x) on Apple Silicon, Homebrew Python 3.
- **Network:** a single flat LAN with the speakers and the Mac on the **same
  subnet** — tested on a `/22` (`192.168.4.0/22`), speakers on Wi-Fi. Discovery is
  a unicast sweep, so the machine must be able to reach the speakers directly
  (same subnet / VLAN, no client isolation).

Reports from other Zipp models, firmwares, and network setups are very welcome —
please open an issue with what worked or didn't, and your `--status` output.

## Troubleshooting

Run `python3 zipp_keepalive.py --status` first — it shows exactly what's reachable.

- **No speakers found?** Almost always network isolation:
  - Speaker and machine must be on the **same subnet / VLAN**. A guest network, a
    separate IoT VLAN, or a different SSID will hide them.
  - Turn off **"AP isolation" / "client isolation"** on your router — it blocks the
    device-to-device traffic this relies on.
  - On a very large subnet the sweep is slower — narrow it with `--cidr`
    (e.g. `--cidr 192.168.1.0/24`).
- **Found but not staying awake?** Check the power column in `--status`: on battery
  it deliberately lets the speaker sleep unless you pass `--nudge-on-battery`.
- **Different Zipp model / firmware?** Command IDs are verified on the Zipp 2 (v879).
  If `--status` finds the speaker but values look off, open an issue with the output.

## Credits

- Protocol reverse-engineering: [Chouffy/python_libratone_zipp](https://github.com/Chouffy/python_libratone_zipp)
  and Benjamin Hanke's original command notes.

## License

MIT — see [LICENSE](LICENSE).
