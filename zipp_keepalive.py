#!/usr/bin/env python3
"""
Libratone Zipp keep-alive daemon  (network / UDP based)
=======================================================

Stops the 3.5mm aux jack from auto-standby (~20 s of silence) by sending the
Libratone "stay active" nudge over UDP to every Libratone on the LAN that is
PLUGGED IN. On battery it stays silent so the speaker's eco-sleep saves the
battery. (Libratone support confirms there's no setting for this - so we nudge
the power state directly over the control protocol instead.)

Design
------
* Discovery  : unicast subnet sweep -> finds every Libratone by current IP.
               DHCP-proof, multi-unit, subnet re-detected each sweep so it
               follows the laptop onto whatever network it's on.
* Power-aware: ChargingStatus (1284): 0 = battery, non-zero = plugged in.
               Nudges plugged-in speakers; on battery it leaves them to sleep
               unless --nudge-on-battery (then respects --battery-floor).
* Quiet hours: optional window (e.g. --quiet-hours 0-8) where nothing is nudged.
* Keep-alive : SET CurrPowerMode=0 (active) + Timer wakeup, every 10 s.
* Logging    : logs only on state changes, to a rotating file (no unbounded log).
* --status   : one-shot; prints what's on the LAN and what it would do, no loop.
* Stdlib only, no dependencies.

Protocol from Chouffy/python_libratone_zipp, verified on Zipp 2 firmware v879.
Ports: send 7777/udp, replies on 7778/udp.
"""

import socket, random, time, threading, subprocess, ipaddress, logging, argparse, sys, os
from logging.handlers import RotatingFileHandler

# ---- configuration ------------------------------------------------------
DEFAULT_CIDR       = "192.168.1.0/24"   # last-resort fallback only; subnet is auto-detected
KEEPALIVE_INTERVAL = 10.0               # seconds between nudges (must be < 20)
DISCOVERY_INTERVAL = 30.0               # seconds between subnet sweeps
SPEAKER_TIMEOUT    = 120.0              # drop a speaker after this long silent
CTRL_PORT, RESULT_PORT = 7777, 7778
LOG_MAX_BYTES      = 1_000_000          # rotate the log at ~1 MB
LOG_BACKUPS        = 2
DEFAULT_BATTERY_FLOOR = 30              # % - with --nudge-on-battery, stop below this

CMD_NAME, CMD_POWER, CMD_CHARGING, CMD_BATTERY, CMD_TIMER = 90, 14, 1284, 256, 15

log = logging.getLogger("zipp")

# ---- packet builder -----------------------------------------------------
def build(command, ctype=1, data=None):
    crc = random.randint(1, 65535).to_bytes(2, "big")
    payload = b"" if data is None else str(data).encode("ascii")
    return (b"\xaa\xaa" + bytes([ctype]) + command.to_bytes(2, "big")
            + b"\x00" + crc + len(payload).to_bytes(2, "big") + payload)

def send(snd, pkt, addr):
    """UDP send that ignores per-host errors (e.g. No route to host on a sweep)."""
    try:
        snd.sendto(pkt, addr)
    except OSError:
        pass

# ---- subnet auto-detection (macOS) --------------------------------------
def local_cidr():
    try:
        rt = subprocess.check_output(["route", "-n", "get", "default"],
                                     text=True, stderr=subprocess.DEVNULL)
        iface = next(l.split(":", 1)[1].strip()
                     for l in rt.splitlines() if "interface:" in l)
        cfg = subprocess.check_output(["ifconfig", iface],
                                      text=True, stderr=subprocess.DEVNULL)
        for l in cfg.splitlines():
            p = l.split()
            if len(p) >= 4 and p[0] == "inet":
                ip, nm = p[1], p[3]
                mask = int(nm, 16) if nm.startswith("0x") else int(ipaddress.ip_address(nm))
                bits = bin(mask).count("1")
                return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False))
    except Exception:
        pass
    return None   # caller falls back to last-known / DEFAULT_CIDR

# ---- quiet hours --------------------------------------------------------
def parse_quiet(s):
    if not s:
        return None
    a, b = s.split("-")
    return (int(a) % 24, int(b) % 24)

def in_quiet(qh):
    if not qh:
        return False
    h = time.localtime().tm_hour
    a, b = qh
    return (a <= h < b) if a <= b else (h >= a or h < b)

# ---- shared speaker state ----------------------------------------------
speakers = {}          # ip -> {"name","charging","battery","last","logged"}
lock = threading.Lock()

def receiver(stop):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", RESULT_PORT))
    except OSError as e:
        log.error(f"cannot bind UDP {RESULT_PORT}: {e}. Another zipp script running? Exiting.")
        stop.set()
        return
    s.settimeout(1.0)
    while not stop.is_set():
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        if len(data) < 10 or data[0:2] != b"\xaa\xaa":
            continue
        cmd = data[3] * 256 + data[4]
        dlen = data[8] * 256 + data[9]
        val = data[10:10 + dlen].decode("ascii", "replace")
        ip = addr[0]
        with lock:
            sp = speakers.setdefault(ip, {"name": "?", "charging": "?",
                                          "battery": "?", "last": 0.0, "logged": None})
            sp["last"] = time.monotonic()
            if cmd == CMD_NAME:     sp["name"] = val
            if cmd == CMD_CHARGING: sp["charging"] = val
            if cmd == CMD_BATTERY:  sp["battery"] = val

def sweep(snd, cidr):
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except (ValueError, TypeError):
        return
    for i, host in enumerate(net.hosts()):
        ip = str(host)
        send(snd, build(CMD_NAME), (ip, CTRL_PORT))
        send(snd, build(CMD_CHARGING), (ip, CTRL_PORT))
        send(snd, build(CMD_BATTERY), (ip, CTRL_PORT))
        if i % 128 == 0:
            time.sleep(0.02)   # gentle pacing

def nudge(snd, ip):
    send(snd, build(CMD_POWER, 2, "0"), (ip, CTRL_PORT))   # SET power mode = active
    send(snd, build(CMD_TIMER, 2, "00"), (ip, CTRL_PORT))  # wakeup / cancel sleep timer

def decide(sp, nudge_on_battery, battery_floor, quiet):
    """Return (action, reason): action is 'nudge', 'sleep', or 'wait'."""
    chg = sp["charging"]
    if chg == "?":
        return ("wait", "probing")
    if quiet:
        return ("sleep", "quiet hours")
    if chg != "0":
        return ("nudge", "plugged in")
    if not nudge_on_battery:
        return ("sleep", "on battery (eco)")
    try:
        lvl = int(sp["battery"])
    except (ValueError, TypeError):
        lvl = None
    if lvl is not None and lvl < battery_floor:
        return ("sleep", f"battery {lvl}% < floor {battery_floor}%")
    return ("nudge", f"on battery {sp['battery']}%")

# ---- --status one-shot --------------------------------------------------
def do_status(cidr, nudge_on_battery, battery_floor, quiet):
    stop = threading.Event()
    threading.Thread(target=receiver, args=(stop,), daemon=True).start()
    time.sleep(0.3)
    snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(2):                 # two passes - a sleepy battery unit can miss one
        sweep(snd, cidr)
        time.sleep(2.5)
    stop.set()
    q = in_quiet(quiet)
    print(f"Zipp keep-alive status - subnet {cidr}"
          f"{'  [QUIET HOURS active]' if q else ''}\n")
    with lock:
        items = sorted(speakers.items(),
                       key=lambda kv: tuple(int(x) for x in kv[0].split('.')))
    if not items:
        print("  (no Libratone speakers answered - are they powered on?)")
        return
    print(f"  {'IP':16}{'NAME':20}{'POWER':10}{'BATT':7}ACTION")
    for ip, sp in items:
        action, reason = decide(sp, nudge_on_battery, battery_floor, q)
        power = "plugged" if sp["charging"] not in ("0", "?") else (
                "battery" if sp["charging"] == "0" else "?")
        print(f"  {ip:16}{sp['name']:20}{power:10}{sp['battery']+'%':7}{action} ({reason})")

# ---- logging setup ------------------------------------------------------
def setup_logging(logfile):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S")
    if sys.stderr and sys.stderr.isatty():           # manual run -> also show on screen
        sh = logging.StreamHandler(sys.stderr); sh.setFormatter(fmt); log.addHandler(sh)
    if logfile:
        try:
            fh = RotatingFileHandler(logfile, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS)
            fh.setFormatter(fmt); log.addHandler(fh)
        except OSError as e:
            sys.stderr.write(f"log file {logfile} unusable: {e}\n")

# ---- main loop ----------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Libratone Zipp keep-alive daemon")
    ap.add_argument("--nudge-on-battery", action="store_true",
                    help="also keep speakers awake on battery (default: plugged-in only)")
    ap.add_argument("--battery-floor", type=int, default=DEFAULT_BATTERY_FLOOR,
                    help=f"with --nudge-on-battery, stop below this %% (default {DEFAULT_BATTERY_FLOOR})")
    ap.add_argument("--quiet-hours", default=None, metavar="A-B",
                    help="skip nudging during these local hours, e.g. 0-8 or 23-6")
    ap.add_argument("--cidr", default=None, help="fix the subnet to scan (default: auto-detect each sweep)")
    ap.add_argument("--interval", type=float, default=KEEPALIVE_INTERVAL,
                    help="seconds between nudges (default 10)")
    ap.add_argument("--logfile", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "zipp-keepalive.log"),
                    help="rotating log file (default: next to the script)")
    ap.add_argument("--status", action="store_true", help="print current LAN status and exit")
    args = ap.parse_args()

    quiet = parse_quiet(args.quiet_hours)

    if args.status:
        cidr = args.cidr or local_cidr() or DEFAULT_CIDR
        do_status(cidr, args.nudge_on_battery, args.battery_floor, quiet)
        return

    setup_logging(args.logfile)
    mode = "all reachable incl. battery" if args.nudge_on_battery else "plugged-in only"
    cidr = args.cidr or local_cidr() or DEFAULT_CIDR
    log.info(f"zipp-keepalive starting; subnet {cidr}; keep-awake: {mode}; "
             f"floor {args.battery_floor}%; quiet {args.quiet_hours or 'off'}; "
             f"nudge {args.interval:.0f}s, sweep {DISCOVERY_INTERVAL:.0f}s")

    stop = threading.Event()
    threading.Thread(target=receiver, args=(stop,), daemon=True).start()
    snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    last_sweep = 0.0
    was_quiet = None
    try:
        while not stop.is_set():
            try:
                now = time.monotonic()
                quiet_now = in_quiet(quiet)
                if quiet and quiet_now != was_quiet:
                    log.info("entering quiet hours" if quiet_now else "leaving quiet hours")
                was_quiet = quiet_now

                if now - last_sweep >= DISCOVERY_INTERVAL:
                    if not args.cidr:                        # re-detect, follow the laptop
                        detected = local_cidr()
                        if detected and detected != cidr:
                            log.info(f"subnet changed: {cidr} -> {detected}")
                            cidr = detected
                    sweep(snd, cidr)
                    last_sweep = now

                with lock:
                    items = list(speakers.items())

                for ip, sp in items:
                    if now - sp["last"] > SPEAKER_TIMEOUT:
                        with lock:
                            speakers.pop(ip, None)
                        if sp["logged"] is not None:
                            log.info(f"{sp['name']}@{ip}: lost (unreachable)")
                        continue
                    send(snd, build(CMD_CHARGING), (ip, CTRL_PORT))
                    send(snd, build(CMD_BATTERY), (ip, CTRL_PORT))
                    action, reason = decide(sp, args.nudge_on_battery, args.battery_floor, quiet_now)
                    if action == "nudge":
                        nudge(snd, ip)
                    state = (action, reason)
                    if state != sp["logged"]:
                        verb = {"nudge": "keeping awake", "sleep": "leaving to sleep",
                                "wait": "discovered"}[action]
                        log.info(f"{sp['name']}@{ip}: {verb} ({reason})")
                        sp["logged"] = state
            except Exception as e:                       # never let the daemon die
                log.error(f"loop error: {e}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        log.info("zipp-keepalive stopped")

if __name__ == "__main__":
    main()
