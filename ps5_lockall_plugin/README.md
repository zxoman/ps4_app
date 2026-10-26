# lockall for PS5 / etaHEN

A persistent etaHEN plugin (daemon) plus the same Python client that lets you
lock or unlock **all** retail games on a jailbroken PS5 over Wi-Fi.

This is the PS5 port of the PS4 GoldHEN plugin in the parent directory. The
main difference is architectural:

| | PS4 / GoldHEN | PS5 / etaHEN |
|---|---|---|
| Plugin model | injected **per title** | **daemon**, runs for the whole session |
| TCP server (9095) | only while a title is running | **always available** |
| Enforce | `sceSystemServiceKillApp` from inside the game | `sceSystemServiceKillApp` from the daemon |
| App id | `sceKernelGetAppInfo(getpid())` (the `GetAppIdOf*` calls fail inside a game) | `sceSystemServiceGetAppIdOfRunningBigApp()` |
| Unlock while locked | FTP fallback (no listener exists) | plain TCP (listener always exists) |

Because the daemon is always up, lock / unlock / status always work directly;
the client's FTP fallback is available but normally unnecessary.

- TCP server on port **9095**, token auth, on-screen notifications.
- While locked, any running game (`PPSA*`, `CUSA*`, ...) is cleanly closed with
  the system "Close Application" call and the user returns to the home screen.
- State persists across reboot/rest mode.

> Tested firmware / etaHEN: **not yet confirmed on hardware** - see
> *Verification status* below. Built against the public
> [ps5-payload-sdk](https://github.com/ps5-payload-dev/sdk) and the
> [etaHEN-Plugins](https://github.com/etaHEN/etaHEN-Plugins) layout.

---

## Layout

```
ps5_lockall_plugin/
  source/main.c        # the whole daemon
  Makefile             # uses toolchain/prospero.mk from ps5-payload-sdk
  make_plugin.py       # prepends the "etaHEN_PLUGIN\0TID\0VER\0" header
  build/
    lockall_ps5.elf    # intermediate
    lockall_ps5.plugin # deploy this
```

---

## Requirements

1. A PS5 running **etaHEN** with plugin support (the build that can load
   `/data/etaHEN/plugins`).
2. The **ps5-payload-sdk** (John's SDK):
   <https://github.com/ps5-payload-dev/sdk/releases>
3. An LLVM/Clang that accepts the `x86_64-sie-ps5` target (Clang 22 works),
   plus `make`, `python3`.
4. `llvm-config` on `PATH` - the SDK's wrapper scripts use it to locate
   `clang`/`ld.lld`.

On Fedora the compiler is available without root:

```bash
dnf download --arch=x86_64 --destdir rpms \
    clang clang-libs clang-resource-filesystem lld lld-libs llvm llvm-libs
for r in rpms/*.rpm; do rpm2cpio "$r" | cpio -idm; done   # into ./llvm
dnf download --arch=x86_64 --destdir rpms make
rpm2cpio rpms/make-*.rpm | cpio -idm                    # into ./tools
```

If your LLVM package does not ship `llvm-config` (some split packages do not),
create a tiny shim next to `clang` - the SDK only calls `--bindir`:

```bash
cat > llvm/usr/bin/llvm-config <<'EOF'
#!/usr/bin/env bash
BINDIR="$(dirname "$(readlink -f "$0")")"
case "$1" in
    --bindir) echo "$BINDIR" ;;
    --prefix) echo "$(dirname "$BINDIR")" ;;
    --version) echo "22.1.8" ;;
    *) exit 0 ;;
esac
EOF
chmod +x llvm/usr/bin/llvm-config
```

---

## Build

```bash
export PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk
# point at the extracted toolchain
export LD_LIBRARY_PATH=/path/to/llvm/usr/lib64:/path/to/llvm/usr/lib
export PATH=/path/to/tools/usr/bin:/path/to/llvm/usr/bin:$PS5_PAYLOAD_SDK/bin:$PATH
export LLVM_CONFIG=/path/to/llvm/usr/bin/llvm-config

cd ps5_lockall_plugin
make
```

Output: `build/lockall_ps5.plugin` (the `.elf` is also kept for inspection).

The plugin title id defaults to `LOCK00001` and the version to `1.00`. You can
override them:

```bash
make TID=LOCK00002 VERSION=1.01
```

The title id must be **4 letters + 5 digits**; the version `x.xx`.

### Sanity checks

```bash
llvm-readelf -d build/lockall_ps5.elf | grep NEEDED
# expect: libSceLibcInternal, libSceSystemService, libSceNet,
#         libSceSysmodule, libSceUserService, libSceNetCtl, libkernel_*
head -c 24 build/lockall_ps5.plugin | od -c   # starts with etaHEN_PLUGIN\0
```

---

## Install

Copy the `.plugin` file to one of the locations etaHEN scans at boot:

- internal: `/data/etaHEN/plugins/lockall_ps5.plugin`
- USB: `<usb-root>/etaHEN/plugins/lockall_ps5.plugin`

Over etaHEN's FTP (if enabled), for example:

```bash
ftp <ps5-ip>
# anonymous / anonymous (etaHEN default)
put build/lockall_ps5.plugin /data/etaHEN/plugins/lockall_ps5.plugin
```

Reboot the console (or reload plugins from the etaHEN toolbox). The plugin
starts its listener on TCP **9095** and logs to klog with the `[lockall]`
prefix.

### Token

The default token is `changeme`. To change it, create
`/data/etaHEN/lockall.ini`:

```ini
token=mysecret
```

and pass the same token to the client (`--token mysecret`).

---

## Usage

The client is shared with the PS4 version:

```bash
python3 ../client/ps4lock.py --ip <ps5-ip> status
python3 ../client/ps4lock.py --ip <ps5-ip> lock
python3 ../client/ps4lock.py --ip <ps5-ip> unlock
```

No game is needed; the daemon is always listening. If you prefer raw sockets:

```bash
printf 'changeme:status\n' | nc <ps5-ip> 9095
printf 'changeme:lock\n'   | nc <ps5-ip> 9095
printf 'changeme:unlock\n' | nc <ps5-ip> 9095
```

Protocol: `TOKEN:command\n`.

| command | reply |
|---|---|
| `lock` | `OK locked` |
| `unlock` | `OK unlocked` |
| `status` | `locked` / `unlocked` |
| bad token / unknown | `ERR` |

If you still want the FTP fallback (e.g. the daemon did not start), point it at
the etaHEN state file:

```bash
python3 ../client/ps4lock.py --ip <ps5-ip> \
    --state-path /data/etaHEN/lockall_state.txt lock
```

State lives in `/data/etaHEN/lockall_state.txt` (`1` / `0`).

---

## How it works

- `main()` starts two threads:
  - **server**: `bind()`/`listen()` on `0.0.0.0:9095` (retries every second if
    etaHEN loads the plugin before the network stack / `/data` is ready), then
    accepts connections and serves the protocol.
  - **monitor**: every 250 ms calls `sceSystemServiceGetAppIdOfRunningBigApp()`.
    If a game is running, the state is locked, and the title id looks like a
    retail game, it shows a notification once and calls
    `sceSystemServiceKillApp(appid, -1, 0, 0)` - the same clean "Close
    Application" path the system uses (no `CE-36329-3`-style shell crash).
- The daemon re-reads the state file every poll, so a lock takes effect on the
  next tick even if it was written over FTP.
- Title prefixes treated as games: `CUSA`, `PPSA`, `PCAS`, `PCSA`, `SCUS`,
  `PPSP`, `PPSX`. Adjust `is_game_title()` if you need more.
- `kill -9`-style force kill is **not** used; the game is closed cleanly.

### Notes from the PS4 version (still relevant)

- Do **not** try to trigger the PS-button/`NavigateToGoHome` flow from a plugin.
  On PS4 that made `SceShellUI` call `SetForegroundUserId` with no foreground
  user and crash the shell (`CE-36329-3`). Clean `KillApp` avoids this.
- On PS5 the running-app queries work from a daemon, which is why the PS5 port
  does not need the `sceKernelGetAppInfo(getpid())` trick the PS4 port uses.

---

## Verification status

- **Build**: verified. Produces a valid `x86_64-sie-ps5` PIE ELF and a
  `.plugin` with the correct header (`lockall_ps5.plugin`, TID `LOCK00001`,
  version `1.00`).
- **Runtime on PS5**: **not yet tested** in this workspace. To validate:
  1. install the plugin and reboot;
  2. `status` should answer without launching a game;
  3. `lock`, launch a game -> it should close within a second and show
     "Games are locked";
  4. `unlock`, launch again -> it should stay;
  5. check klog for `[lockall]` lines (`listening on port 9095`,
     `close app=0x... tid=... ret=0x...`).

Please report your PS5 firmware and etaHEN version when testing - the system
service stubs/behaviour can differ between firmware ranges.

---

## Troubleshooting

**Nothing listens on 9095 / connection refused**
- The plugin is not loaded. Check the file is in `/data/etaHEN/plugins/`, has
  the `.plugin` extension, and that your etaHEN build supports plugins.
- Check klog for `[lockall] ps5 lockall plugin starting`.
- Make sure no other plugin uses port 9095.

**`ERR`**
- Token mismatch. Compare `--token` with `/data/etaHEN/lockall.ini`.

**Game does not close**
- `status` must be `locked`.
- Check the title id prefix; if it is not in `is_game_title()`, add it.
- Look for `[lockall] close app=... ret=...` in klog. A non-zero `ret` means the
  system service refused the call (report the value).

**No notification**
- Cosmetic only; enforcement still works. Notifications can be filtered by the
  system in some contexts.

**Plugin starts before the network is up**
- Handled: the server retries `bind()` every second. The state file defaults to
  `unlocked` until `/data` is available.

---

## Credits / licences

- SDK: [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk) (MIT).
- Plugin header/layout: [etaHEN/etaHEN-Plugins](https://github.com/etaHEN/etaHEN-Plugins)
  (GPL-3.0); `make_plugin.py` here is a small reimplementation of the same
  header format.
- PS4 original in the parent directory.
