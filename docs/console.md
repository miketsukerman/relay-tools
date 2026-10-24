# Board serial console (`relay-console`)

`relay-console` gives people and AI agents safe access to the debug UART of a
board under test. It is the **only** process that opens each board's serial
port. It records every line to disk — timestamped, cleaned and split into
boots — and exposes two things over HTTP:

- **Reading:** bounded log views, a parsed *boot report* per boot, and a
  blocking *wait for pattern* call.
- **Asking:** a small set of **named, pre-approved console actions** defined
  in the board profile (for example `ip_address`), whose output is cut out of
  the stream and parsed. The main use is reading the DUT's IP address and
  checking its state when the network is unreachable.

There is **no free-form terminal**. The only bytes ever written to the serial
port are the login sequence, a single carriage return used to probe the
console state, and the commands of named actions. Agents cannot add or change
actions; only the board profile on the Pi defines them.

## Quick start

1. Add a `console:` block to the board profile in `/etc/relay/boards.d/`
   (schema below).
2. Start the service (see the shipped `relay-console.service` unit):

   ```sh
   relay-console --boards-dir /etc/relay/boards.d \
                 --data-dir /var/lib/relay/console
   ```

3. Read it:

   ```sh
   relay-board lab console boots
   relay-board lab console report
   relay-board lab console log --tail 100 --grep "FAILED"
   relay-board lab console state
   relay-board lab console ip
   relay-board lab console run system_status
   relay-board lab console follow        # live read-only mirror
   ```

`relay-mcp` exposes the same functionality to agents as the tools
`list_boots`, `console_log`, `boot_report`, `wait_for_console`,
`console_state`, `dut_ip_address`, `list_console_actions` and
`run_console_action`.

## Profile schema: the `console:` block

```yaml
console:
  device: /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10KXXXX-if00-port0
  baud: 115200                 # default 115200
  data_bits: 8                 # 5-8, default 8
  parity: none                 # none | even | odd, default none
  stop_bits: 1                 # 1 | 2, default 1
  boot_banner: "U-Boot SPL"    # regex: a new boot starts here even without a marker
  prompts:
    uboot: "=> "               # regex
    login: "login:"            # regex; seeing it means the boot succeeded
  patterns:                    # optional, merged over the built-in defaults
    errors: ["Kernel panic", "\\[FAILED\\]"]
    warnings: ["rcu: INFO", "watchdog"]
  redact: ["(?i)password[:=]\\s*\\S+"]   # regexes; matches become [REDACTED]
  retention:
    max_boots: 200             # default 200
    max_megabytes: 200         # default 200; whichever limit is hit first
  mirror_port: 7001            # optional read-only TCP mirror (nc pi 7001)
  login:                       # needed for Linux actions on a login prompt
    user: root
    password_file: /etc/relay/secrets/lab.console   # or password_env
    shell_prompt: "[#$] $"     # regex for the end of a shell prompt line
    timeout_s: 10
    logout_after: false        # true: send "exit" after each action
  actions:                     # merged over the built-in actions
    uboot_net:
      context: uboot           # linux | uboot
      command: "printenv ipaddr serverip ethaddr"
      parse: kv                # ipv4 | systemd | kv | lines
      timeout_s: 5
      description: U-Boot network environment
    dmesg_tail: null           # null disables a built-in action
```

Notes:

- **`device`** is required: an absolute device path or a pyserial URL
  (`rfc2217://host:port` or `socket://host:port`). Prefer the stable
  `/dev/serial/by-id/...` symlinks; `/dev/ttyUSB*` and `/dev/ttyACM*` change
  their numbering across replugs and relay-console logs a warning for them.
  `deploy/systemd/99-relay-console.rules` shows how to pin an adapter with a
  udev rule. With a URL, `baud`/`parity` are sent via RFC 2217 where
  supported and otherwise ignored.
- **`login.password_file`** must be an absolute path, owned appropriately and
  not readable by others (mode `0640` or stricter). It is read only when
  logging in, never at parse time. The literal password is added to the
  redaction rules automatically, so it never appears in logs or results.
- **`mirror_port`** must be 1024–65535 and unique across all loaded profiles.
- **Mutation lint:** actions are meant to be read-only. A command containing
  output redirection (`>`), `rm `, `dd `, `reboot`, `poweroff`, `shutdown`,
  `halt`, `mkfs`, `fw_setenv`, `setenv`, `saveenv`, `mmc write`, `erase` or
  `| sh` is refused unless the action sets `mutating: true`. relay-mcp hides
  mutating actions unless started with `--allow-mutating-console-actions`.

## Built-in actions

| Name | Context | Command | Parse |
|---|---|---|---|
| `ip_address` | linux | `ip -4 -o addr show scope global` | `ipv4` |
| `network_status` | linux | `ip -o link show; ip route show default` | `lines` |
| `system_status` | linux | `systemctl is-system-running; systemctl --failed --no-legend --plain` | `systemd` |
| `uptime` | linux | `cat /proc/uptime` | `lines` |
| `os_release` | linux | `cat /etc/os-release` | `kv` |
| `kernel` | linux | `uname -a` | `lines` |
| `dmesg_tail` | linux | `dmesg \| tail -n 50` | `lines` |
| `uboot_net` | uboot | `printenv ipaddr serverip ethaddr` | `kv` |

A profile can override any of these under `actions:` or disable one with
`name: null`.

## Boots, markers and reports

Every line is sanitized (ANSI escapes and control characters stripped, long
lines truncated, `redact` patterns applied) and stored under
`<data-dir>/<board>/boots/NNNNNN.log` with a UTC millisecond timestamp per
line. `boots.jsonl` indexes the boots.

A new boot starts when:

- **a marker arrives** — `BoardController` posts one right before any step
  that powers on, power-cycles or resets the board, so each boot is linked to
  who triggered it (`relay-board`, `relay-mcp` plus its audit id);
- **the `boot_banner` regex matches** with no marker in the last 10 seconds —
  self-reboots and watchdog resets are split too;
- **the service starts**.

The *boot report* summarises a boot: `result` (`login_reached`,
`kernel_panic`, `uboot_prompt`, `no_kernel`, `in_progress`, `incomplete`),
U-Boot and kernel version lines, boot times, capped error/warning lists with
context, and `addresses_seen`.

### Passive IP addresses: `addresses_seen`

The report lists every IPv4 address found in lines matching `inet `,
`IP address`, `ipaddr=`, `DHCP` or `bound to`. If your image adds `\4` to
`/etc/issue`, the login prompt prints the board's address, so it shows up in
`addresses_seen` without running any action — useful when nobody can log in.

## HTTP API

`relay-console` listens on `127.0.0.1:8001` by default. A bearer token from
`RELAY_CONSOLE_TOKEN` is required when binding to a non-loopback host.

| Endpoint | Purpose |
|---|---|
| `GET /consoles` | Boards with console capture, device, connected, current boot |
| `POST /consoles/{board}/markers` | `{reason, actor, audit_id?}` → new boot id |
| `GET /consoles/{board}/boots?limit=10` | Newest boots first |
| `GET /consoles/{board}/boots/{id\|last}/log?tail=200&grep=&since_line=` | Bounded lines |
| `GET /consoles/{board}/boots/{id\|last}/report` | Parsed boot report |
| `GET /consoles/{board}/wait?pattern=&timeout_s=120` | Block until a line matches |
| `GET /consoles/{board}/actions` | The board's named actions |
| `POST /consoles/{board}/state` | Console state probe (writes one `\r`) |
| `POST /consoles/{board}/actions/{name}` | Run one named action |

## Read-only mirror for people

With `mirror_port` set, the sanitized live stream is served to TCP clients
(at most 4 per board), read-only — anything a client types is discarded:

```sh
nc raspberrypi 7001          # or: relay-board lab console follow
```

## ser2net option: sharing the UART

If you want ser2net to own the physical UART (for its tracing, or to feed
other consumers), let relay-console connect to it over RFC 2217 instead of
opening the device itself:

```yaml
# /etc/ser2net.yaml
connection: &lab
  accepter: telnet(rfc2217),tcp,127.0.0.1,2001
  connector: serialdev,/dev/serial/by-id/usb-FTDI_..._-if00-port0,115200n81,local
  options:
    max-connections: 1
    # optional raw backup log:
    # trace-both: /var/log/ser2net/lab-%Y%m%d.log
```

and in the board profile:

```yaml
console:
  device: rfc2217://127.0.0.1:2001
```

**Warning:** keep ser2net on localhost and `max-connections: 1`. If ser2net
allows more than one connection, every connected client can type, so a person
attached through ser2net can interleave keystrokes with a running console
action and corrupt it. People should watch through relay-console's read-only
mirror (`mirror_port`) instead.

## Security model

- Agents never get a raw terminal. The MCP tools return bounded views and
  parsed results only, in fields named `untrusted_log` / `untrusted_output`:
  console output is untrusted data from the device, and tool descriptions
  tell the model never to follow instructions that appear in it.
- Only the board profile on the Pi defines actions; the mutation lint refuses
  obviously destructive commands unless explicitly marked `mutating: true`,
  and relay-mcp hides mutating actions by default.
- Passwords are read from a root-owned file at login time, redacted from
  every log and result, and never echoed back.
- A stuck serial port never blocks relay switching: relay-console is a
  separate service, and boot markers are posted with a 2 s timeout and
  ignored on failure.
