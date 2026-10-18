/*
 * lockall - GoldHEN plugin: parental "lock all games" switch over TCP.
 *
 * Target: PS4 (Orbis OS), GoldHEN plugin SDK + OpenOrbis PS4 Toolchain.
 *
 * Architecture
 * ------------
 * Installed through /data/GoldHEN/plugins.ini under [default], so the loader
 * starts one instance of this PRX inside every process it is injected into
 * (games, homebrew, big apps). Because each process has its own address space
 * and the "locked" flag must be shared, the flag lives in a file on disk
 * (/data/GoldHEN/lockall_state.txt). A file is used instead of shared memory
 * because it trivially gives us persistence across reboot / rest mode too.
 *
 * Only ONE process can bind TCP port 9095 at a time, so the first instance to
 * start wins the listener; the others run the monitor only and periodically
 * retry the bind, taking over if the listener process dies. This avoids the
 * need for a cross-process leader-election protocol.
 *
 * The monitor thread is what actually enforces the lock: each instance checks
 * its OWN title id. If it is a retail game (CUSA*) and the state file says
 * locked, it notifies the user and closes itself via the system service.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>

#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>

#include <orbis/libkernel.h>

#include <Common.h>

#define PLUGIN_NAME  "lockall"
#define PLUGIN_DESC  "Parental lock-all-games switch over TCP"
#define PLUGIN_AUTH  "built for ps4_app"
#define PLUGIN_VER   0x00000100

#define LOCK_PORT    9095
#define MAX_CMD      128

#define TOKEN_PATH   "/data/GoldHEN/lockall.ini"
#define STATE_PATH   "/data/GoldHEN/lockall_state.txt"
#define DEFAULT_TOKEN "changeme"

#define ICON_SYS     "cxml://psnotification/tex_icon_system"

/* attribute helpers (same definitions the official SDK samples use) */
#define attr_module_hidden __attribute__((weak)) __attribute__((visibility("hidden")))
#define attr_public        __attribute__((visibility("default")))

/* ------------------------------------------------------------------ */
/* plugin metadata exported for the GoldHEN plugin loader             */
/* ------------------------------------------------------------------ */
attr_public const char *g_pluginName = PLUGIN_NAME;
attr_public const char *g_pluginDesc = PLUGIN_DESC;
attr_public const char *g_pluginAuth = PLUGIN_AUTH;
attr_public uint32_t    g_pluginVersion = PLUGIN_VER;

/* ------------------------------------------------------------------ */
/* global state                                                       */
/* ------------------------------------------------------------------ */
static volatile int  g_running   = 0;
static volatile int  g_listen_fd = -1;
static OrbisPthread  g_server_tid;
static OrbisPthread  g_monitor_tid;
static int           g_threads_started = 0;

/* System service functions are resolved at runtime with sys_dynlib_load_prx
 * + sys_dynlib_dlsym, the same way the official button_swap plugin does it.
 * This avoids a hard load-time dependency on libSceSystemService being
 * resident inside whatever process we get injected into. */
typedef int32_t (*fn_kill_app_t)(uint32_t appid, int32_t opt, int32_t method, int32_t reason);
typedef int32_t (*fn_get_big_app_t)(void);
typedef void    (*fn_go_home_t)(void);

static fn_kill_app_t    p_KillApp    = NULL;
static fn_get_big_app_t p_GetBigApp  = NULL;
static fn_go_home_t     p_GoHome     = NULL;
static int              g_sys_loaded = 0;

static void logf_(const char *fmt, ...);

static void load_system_service(void) {
    if (g_sys_loaded) return;
    int h = 0;
    if (sys_dynlib_load_prx("libSceSystemService.sprx", &h) != 0 || h == 0) {
        logf_("libSceSystemService.sprx not available\n");
        return;
    }
    sys_dynlib_dlsym(h, "sceSystemServiceKillApp", (void *)&p_KillApp);
    sys_dynlib_dlsym(h, "sceSystemServiceGetAppIdOfBigApp", (void *)&p_GetBigApp);
    sys_dynlib_dlsym(h, "sceSystemServiceNavigateToGoHome", (void *)&p_GoHome);
    g_sys_loaded = 1;
    logf_("system service resolved (kill=%p bigapp=%p home=%p)\n",
          (void *)p_KillApp, (void *)p_GetBigApp, (void *)p_GoHome);
}

static void logf_(const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    printf("[lockall] %s", buf);
}

/* on-screen toast, same layout used by the official samples */
static void notify(const char *msg) {
    OrbisNotificationRequest req;
    memset(&req, 0, sizeof(req));
    req.type = NotificationRequest;
    req.unk3 = 0;
    req.useIconImageUri = 1;
    req.targetId = -1;
    strncpy(req.message, msg, sizeof(req.message) - 1);
    strncpy(req.iconUri, ICON_SYS, sizeof(req.iconUri) - 1);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

/* ------------------------------------------------------------------ */
/* file IO                                                            */
/*                                                                    */
/* stdio (fopen/fread/...) is NOT safe here: inside a plugin injected  */
/* into a game process it faults in the kernel copyin path (SIGBUS).   */
/* The GoldHEN plugin loader uses the raw sceKernel* file syscalls for */
/* the very same /data files for this reason, so we do too.            */
/* ------------------------------------------------------------------ */
static int read_file(const char *path, char *buf, size_t buflen) {
    int fd = sceKernelOpen(path, O_RDONLY, 0);
    if (fd < 0) return -1;

    size_t n = sceKernelRead(fd, buf, buflen - 1);
    sceKernelClose(fd);

    if (n == 0 || n >= buflen) return -1;
    buf[n] = '\0';
    return (int)n;
}

static int load_locked(void) {
    char buf[16];
    if (read_file(STATE_PATH, buf, sizeof(buf)) < 0) return 0;
    return buf[0] == '1' ? 1 : 0;
}

static void save_locked(int locked) {
    int fd = sceKernelOpen(STATE_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0777);
    if (fd < 0) {
        logf_("cannot write state file %s (0x%08x)\n", STATE_PATH, fd);
        return;
    }
    char b[4] = { locked ? '1' : '0', '\n', 0, 0 };
    sceKernelWrite(fd, b, 2);
    sceKernelClose(fd);
}

/* ------------------------------------------------------------------ */
/* token file: accepts either a bare token line, or "token=..."       */
/* ------------------------------------------------------------------ */
static void load_token(char *out, size_t outlen) {
    strncpy(out, DEFAULT_TOKEN, outlen - 1);
    out[outlen - 1] = '\0';

    char buf[512];
    if (read_file(TOKEN_PATH, buf, sizeof(buf)) < 0) return;

    /* only look at the first line */
    char *nl = strpbrk(buf, "\r\n");
    if (nl) *nl = '\0';

    char *val = buf;
    char *eq = strchr(buf, '=');
    if (eq) val = eq + 1;
    while (*val == ' ' || *val == '\t') val++;
    size_t len = strlen(val);
    while (len && (val[len - 1] == ' ' || val[len - 1] == '\t')) val[--len] = '\0';
    if (*val) {
        strncpy(out, val, outlen - 1);
        out[outlen - 1] = '\0';
    }
}

/* ------------------------------------------------------------------ */
/* process identity                                                   */
/* ------------------------------------------------------------------ */
static int current_titleid(char *out, size_t outlen) {
    struct proc_info info;
    memset(&info, 0, sizeof(info));
    if (sys_sdk_proc_info(&info) == 0 && info.titleid[0]) {
        strncpy(out, info.titleid, outlen - 1);
        out[outlen - 1] = '\0';
        return 0;
    }
    /* fallback: ask libkernel directly */
    OrbisAppInfo ai;
    memset(&ai, 0, sizeof(ai));
    if (sceKernelGetAppInfo(getpid(), &ai) == 0) {
        strncpy(out, ai.TitleId, outlen - 1);
        out[outlen - 1] = '\0';
        return 0;
    }
    out[0] = '\0';
    return -1;
}

static int is_game(const char *titleid) {
    return titleid && titleid[0] && strncmp(titleid, "CUSA", 4) == 0;
}

/* never touch anything that is clearly system / injector software */
static int is_protected_process(void) {
    struct proc_info info;
    memset(&info, 0, sizeof(info));
    if (sys_sdk_proc_info(&info) != 0) return 0;
    if (strstr(info.name, "GoldHEN")) return 1;
    if (strstr(info.name, "lockall")) return 1;
    if (strstr(info.path, "lockall")) return 1;
    if (strstr(info.name, "SceShell")) return 1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* kicking the user out of the running game                           */
/*                                                                    */
/* The lock does NOT terminate the title: it sends the user back to    */
/* the home screen (the game stays suspended in the background). The   */
/* monitor keeps doing this while the lock is on, so re-opening the    */
/* game just bounces the user home again.                              */
/* ------------------------------------------------------------------ */
static void exit_to_home(void) {
    load_system_service();

    if (p_GoHome) {
        p_GoHome();
        logf_("navigated to the home screen\n");
        return;
    }

    /* last resort if the go-home entry point could not be resolved:
     * method 0x20000 is the system's own "close, then show home" path. */
    int32_t appid = p_GetBigApp ? p_GetBigApp() : -1;
    if (appid > 0 && p_KillApp) {
        int32_t ret = p_KillApp((uint32_t)appid, 0, 0x20000, 0);
        logf_("go-home unavailable, KillApp(0x%08x) = 0x%08x\n", appid, ret);
    } else {
        logf_("no way to leave the game (go-home and kill both unavailable)\n");
    }
}

/* ------------------------------------------------------------------ */
/* TCP connection handler                                             */
/* ------------------------------------------------------------------ */
static void send_str(int fd, const char *s) {
    size_t len = strlen(s);
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, s + sent, len - sent, 0);
        if (n <= 0) return;
        sent += (size_t)n;
    }
}

static void handle_client(int fd) {
    char buf[MAX_CMD + 1];
    size_t used = 0;
    memset(buf, 0, sizeof(buf));

    /* read until newline or MAX_CMD; enforce the 128 byte cap */
    while (used < MAX_CMD) {
        char c;
        ssize_t n = recv(fd, &c, 1, 0);
        if (n == 0) break;
        if (n < 0) { logf_("recv failed (%d)\n", (int)n); return; }
        if (c == '\n' || c == '\r') break;
        buf[used++] = c;
    }
    buf[used] = '\0';

    if (used == 0) return;

    char *sep = strchr(buf, ':');
    if (!sep) {           /* malformed: no token separator */
        send_str(fd, "ERR");
        return;
    }
    *sep = '\0';
    const char *token = buf;
    const char *cmd   = sep + 1;

    char expected[128];
    load_token(expected, sizeof(expected));
    if (strcmp(token, expected) != 0) {
        logf_("rejecting command with bad token\n");
        send_str(fd, "ERR");
        return;
    }

    if (strcmp(cmd, "lock") == 0) {
        save_locked(1);
        notify("Games are locked");
        send_str(fd, "OK locked");
    } else if (strcmp(cmd, "unlock") == 0) {
        save_locked(0);
        send_str(fd, "OK unlocked");
    } else if (strcmp(cmd, "status") == 0) {
        send_str(fd, load_locked() ? "locked" : "unlocked");
    } else {
        send_str(fd, "ERR");
    }
}

/* ------------------------------------------------------------------ */
/* server thread: owns the listener when it can bind                  */
/* ------------------------------------------------------------------ */
static void *server_thread(void *arg) {
    (void)arg;

    while (g_running) {
        int srv = socket(AF_INET, SOCK_STREAM, 0);
        if (srv < 0) {
            logf_("socket() failed (%d)\n", srv);
            sceKernelUsleep(1000 * 1000);
            continue;
        }

        int yes = 1;
        setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

        struct sockaddr_in addr;
        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_port = htons(LOCK_PORT);
        addr.sin_addr.s_addr = htonl(INADDR_ANY);

        if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
            /* another instance is already the listener */
            close(srv);
            sceKernelUsleep(3000 * 1000);
            continue;
        }
        if (listen(srv, 1) != 0) {
            logf_("listen() failed\n");
            close(srv);
            sceKernelUsleep(3000 * 1000);
            continue;
        }

        g_listen_fd = srv;
        logf_("listening on port %d\n", LOCK_PORT);

        while (g_running) {
            fd_set rfds;
            FD_ZERO(&rfds);
            FD_SET(srv, &rfds);
            struct timeval tv;
            tv.tv_sec = 1;
            tv.tv_usec = 0;

            int sel = select(srv + 1, &rfds, NULL, NULL, &tv);
            if (sel < 0) {
                break;
            }
            if (sel == 0) continue;   /* timeout, re-check g_running */

            int cli = accept(srv, NULL, NULL);
            if (cli < 0) {
                continue;   /* listener still valid; retry */
            }

            handle_client(cli);       /* one connection at a time */
            close(cli);
        }

        if (g_listen_fd == srv) g_listen_fd = -1;
        close(srv);
    }
    return NULL;
}

/* ------------------------------------------------------------------ */
/* monitor thread: enforces the lock for this process                 */
/* ------------------------------------------------------------------ */
static void *monitor_thread(void *arg) {
    (void)arg;

    char titleid[32];
    int self_is_game = 0;

    if (current_titleid(titleid, sizeof(titleid)) == 0 && is_game(titleid)) {
        self_is_game = 1;
        logf_("running inside game %s\n", titleid);
    } else {
        logf_("running in non-game process '%s'\n", titleid);
    }

    while (g_running) {
        if (load_locked()) {
            if (self_is_game && !is_protected_process()) {
                notify("Games are locked");
                exit_to_home();
                /* cooldown: avoid notification/go-home spam */
                sceKernelUsleep(2000 * 1000);
            }
        }
        sceKernelUsleep(500 * 1000);
    }
    return NULL;
}

/* ------------------------------------------------------------------ */
/* lifecycle                                                          */
/* ------------------------------------------------------------------ */
static int start_threads(void) {
    if (g_threads_started) return 0;

    g_running = 1;

    if (scePthreadCreate(&g_server_tid, NULL, server_thread, NULL, "lockall_tcp") != 0) {
        logf_("failed to create server thread\n");
        g_running = 0;
        return -1;
    }
    if (scePthreadCreate(&g_monitor_tid, NULL, monitor_thread, NULL, "lockall_mon") != 0) {
        logf_("failed to create monitor thread\n");
        g_running = 0;
        scePthreadJoin(g_server_tid, NULL);
        return -1;
    }
    g_threads_started = 1;
    return 0;
}

static void stop_threads(void) {
    if (!g_threads_started) return;

    g_running = 0;
    /* nudge select() so the server thread wakes promptly */
    if (g_listen_fd >= 0) {
        shutdown(g_listen_fd, SHUT_RDWR);
    }

    scePthreadJoin(g_server_tid, NULL);
    scePthreadJoin(g_monitor_tid, NULL);
    g_threads_started = 0;
    logf_("threads stopped\n");
}

int32_t attr_public plugin_load(int32_t argc, const char* argv[]) {
    (void)argc; (void)argv;
    logf_("%s plugin starting (ver 0x%08x)\n", PLUGIN_NAME, PLUGIN_VER);

    load_system_service();

    /* make sure the state file exists so a fresh install is well-defined */
    char probe[8];
    if (read_file(STATE_PATH, probe, sizeof(probe)) < 0) {
        save_locked(0);
    }

    if (start_threads() != 0) {
        notify("LockAll: failed to start");
        return -1;
    }

    notify("LockAll active");
    return 0;
}

int32_t attr_public plugin_unload(int32_t argc, const char* argv[]) {
    (void)argc; (void)argv;
    logf_("%s plugin stopping\n", PLUGIN_NAME);
    stop_threads();
    return 0;
}

/* module_start/module_stop run from sceKernelLoadStartModule, before the
 * plugin loader has called plugin_load. Doing any real work here (threads,
 * sockets, file IO) crashes the host title, so keep them as no-ops. The
 * loader starts us via plugin_load and stops us via plugin_unload. */
int32_t attr_module_hidden module_start(int64_t argc, const void *args) {
    (void)argc; (void)args;
    return 0;
}

int32_t attr_module_hidden module_stop(int64_t argc, const void *args) {
    (void)argc; (void)args;
    return 0;
}
