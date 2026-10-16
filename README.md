# lockall - PS4 GoldHEN parental "lock all games" plugin

A GoldHEN plugin (`.prx`) plus a small Python PC client that lets you lock or
unlock **all** retail games on a jailbroken PS4 over Wi-Fi.

- Plugin: background TCP server on port **9095** (not 9090 - that is GoldHEN's
  BinLoader), token authentication, on-screen notifications, closes any running
  game (`CUSA*`) while locked, and persists its state across reboot/rest mode.
- Client: `ps4lock.py lock|unlock|status`.

> Firmware / GoldHEN used while writing this: **9.00 / GoldHEN 2.4b18.6**
> (fill in your own). Everything below assumes a working GoldHEN with the
> *Plugin Loader* enabled.

---

## Repository layout

```
lockall_plugin/
  Makefile                     # based on the official plugin_template Makefile
  source/main.c                # the whole plugin
  config/lockall.ini           # example token file
  config/plugins.ini.example   # example plugins.ini snippet
client/
  ps4lock.py                   # PC client
README.md
```

---

## How it works (and an important limitation)

GoldHEN injects plugins **per process**. `plugins.ini` `[default]` means the
plugin is loaded into every title the plugin loader runs in - i.e. every game
or app you launch.

- Each instance reads its own title id.
- The **state** (`locked` / `unlocked`) is stored in a file on disk
  (`/data/GoldHEN/lockall_state.txt`) so all instances agree on it and it
  survives reboot/rest mode.
- Only one process may bind TCP 9095; whichever instance starts first wins the
  listener. The others run the monitor only and retry the bind every ~3 s, so
  if the listener process exits another instance takes over automatically.
- The **monitor** in each instance checks every 500 ms: if the state is
  *locked* **and** this process is a retail game (`CUSA*`), it shows
  "Games are locked" and closes the game.

**Limitation to be aware of:** because plugins only exist inside launched
titles, the TCP server is reachable while a GoldHEN-injected title (a game or
app) is running. If the console is sitting on the home screen with *nothing*
launched and GoldHEN did not inject into the shell process, port 9095 will not
answer. In that case open any app/game briefly to bring an instance (and the
server) up. This is a GoldHEN plugin architecture constraint, not a bug in this
plugin. See *Troubleshooting*.

---

## Building

### Requirements

- [OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain)
  (`clang`, `ld.lld`, `create-fself`).
- [GoldHEN Plugins SDK](https://github.com/GoldHEN/GoldHEN_Plugins_SDK) cloned
  locally. This project compiles the two SDK source files it needs
  (`GoldHEN.c`, `Syscall.c`) directly, so you do **not** need to pre-build
  `libGoldHEN_Hook.a`.

```bash
# 1. get the SDK if you don't have it
git clone https://github.com/GoldHEN/GoldHEN_Plugins_SDK.git ~/GoldHEN_Plugins_SDK

# 2. point the build at your toolchain + SDK
export OO_PS4_TOOLCHAIN="$HOME/openorbis"
export GOLDHEN_SDK="$HOME/GoldHEN_Plugins_SDK"

# 3. build (produces build/lockall.prx)
cd lockall_plugin
make
```

The build produces:

- `build/lockall.prx`  - the file you install
- `build/lockall.oelf` - intermediate, not needed on the console

Useful flags:

```bash
make DEBUG=1     # debug build (-D__FINAL__=0)
make clean
```

---

## Installing on the PS4

1. Copy the built PRX to the console's data partition
   (via GoldHEN's FTP server on port 2121, or a file manager such as Itemzflow):

   ```
   build/lockall.prx  ->  /data/GoldHEN/plugins/lockall.prx
   ```

2. Make sure GoldHEN's **Plugin Loader** is enabled
   (*Settings -> GoldHEN -> Plugin Settings -> Enable Plugin Loader*).

3. Edit `/data/GoldHEN/plugins.ini` and add the plugin under `[default]`
   so it loads for all titles (see `config/plugins.ini.example`):

   ```ini
   [default]
   /data/GoldHEN/plugins/lockall.prx
   ```

4. (Recommended) create your token file at `/data/GoldHEN/lockall.ini`:

   ```ini
   token=mysecret
   ```

   If you skip this, the token is the default `changeme`.

5. Start a game or app. On load the plugin shows a "LockAll active"
   notification.

> Only one path per line. A bare path means "enabled"; you can also write
> `path=true` / `path=false`.

---

## PC client

No third-party packages required (standard library only, Python 3.6+).

```bash
cd client

# edit PS4_IP / TOKEN at the top of ps4lock.py, or pass them as options:
python3 ps4lock.py --ip 192.168.1.50 --token mysecret lock
python3 ps4lock.py --ip 192.168.1.50 --token mysecret status
python3 ps4lock.py --ip 192.168.1.50 --token mysecret unlock
```

Expected replies:

| command  | reply          |
|----------|----------------|
| `lock`   | `OK locked`    |
| `unlock` | `OK unlocked`  |
| `status` | `locked` / `unlocked` |
| bad token / bad command | `ERR` |

---

## Troubleshooting

### The plugin doesn't load at all

- Confirm *Plugin Loader* is enabled in GoldHEN settings.
- Check `plugins.ini` has the exact path `/data/GoldHEN/plugins/lockall.prx`
  under `[default]` (leading `/`, no quotes, no stray spaces).
- Confirm the filename on disk matches case exactly (`lockall.prx`).
- Watch GoldHEN's klog (server on TCP 3232, e.g.
  `telnet <ps4-ip> 3232`). The plugin logs lines prefixed `[lockall]`.
- Very old GoldHEN builds treat an entry's value differently; use a bare path
  (works everywhere) rather than `=true`.

### "connection refused" from the client

- The server only exists while a title that GoldHEN injected the plugin into
  is running. Launch any game or app, then retry. (See the limitation above.)
  Because the monitor kills a game within ~500 ms while locked, you may need to
  retry `unlock` while a title is starting.
- Verify the port: **9095**, not 9090 (GoldHEN BinLoader) or 9091.
- Confirm the PS4 and PC are on the same network and the PS4 has an IP
  (`Settings -> Network`). Some routers isolate wireless clients from each
  other ("AP isolation") - disable that or use Ethernet.
- Firewall on the PS4 side isn't a thing, but your PC's outbound firewall could
  block it.

### "timeout"

- Usually the same reachability issue as *connection refused*.
- Increase `--timeout` if the console is busy.

### The game isn't being closed

- Only title ids starting with `CUSA` are targeted. Games from other regions
  use `PLAS`/`PCAS`/`PPSA` etc. and are **not** closed by this build; edit
  `is_game()` in `source/main.c` if you need other prefixes.
- The plugin first tries `sceSystemServiceKillApp`. If the firmware rejects
  it, it falls back to `sceSystemServiceNavigateToGoHome` (which suspends the
  game to the background rather than tearing it down), and finally to killing
  its own pid if it runs inside the game process.
- If closing never happens, the monitor instance may not be running inside the
  game process. Ensure the plugin is in `[default]`.

### Firmware differences

- The GoldHEN SDK uses syscall 500 for its RPC; it exists on all GoldHEN
  builds that ship the plugin SDK (2.3+).
- `sceSystemServiceKillApp` argument conventions are the main firmware-varying
  piece. This plugin calls `KillApp(appid, 0, 0x20000, 0)` (the "close to home
  screen" method) and falls back gracefully if the return value is non-zero.
- `libSceSystemService.sprx` is loaded at runtime via
  `sys_dynlib_load_prx`, the same approach the official `button_swap` plugin
  uses, so no link-time dependency is baked in.
- Lowest realistic target: GoldHEN 2.3 (first release with the plugin SDK).
  Tested by the author only insofar as it compiles/links - see below.

---

## Testing status / honesty

- **Compiled and linked successfully** against the real OpenOrbis toolchain
  headers, the GoldHEN Plugins SDK, and `clang`/`ld.lld`, producing a valid
  PS4 SELF (`.prx`) with `plugin_load` / `plugin_unload` exported.
- **Not tested on real PS4 hardware.** The following are unverified:
  - actual runtime behaviour of `sceSystemServiceKillApp` on your firmware,
  - whether the TCP server stays reachable at the home screen,
  - notifications, rest-mode persistence, and thread teardown timing.
- The Python client logic is straightforward but was not run against a live
  plugin.

If something misbehaves, the `[lockall]` klog lines will say what the plugin
decided and which function pointers it resolved.

---

## Protocol (for reference)

```
request:  <TOKEN>:<command>\n          (max 128 bytes, one connection at a time)
response: OK locked | OK unlocked | locked | unlocked | ERR
```

## Credits

Builds on the [GoldHEN Plugins SDK](https://github.com/GoldHEN/GoldHEN_Plugins_SDK)
and the [OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain).
The `sys_dynlib_load_prx` + `sys_dynlib_dlsym` pattern follows the official
`button_swap` plugin.
