/*
 * lockall for PS5 / etaHEN
 *
 * A small persistent daemon ("etaHEN plugin") that provides a parental
 * "lock all games" switch controlled from a PC over Wi-Fi.
 *
 * Unlike the PS4 GoldHEN version - which is injected into every title and
 * therefore lives inside the very game it must close - an etaHEN plugin is a
 * daemon that runs for the whole session. That means the TCP server is always
 * available (so lock/unlock/status always work) and the monitor can watch for
 * a launched game and close it.
 *
 * Protocol (TCP port 9095):  "<token>:<command>\n"
 *   lock    -> "OK locked"
 *   unlock  -> "OK unlocked"
 *   status  -> "locked" / "unlocked"
 *   bad     -> "ERR"
 *
 * State is a tiny file so it survives reboots/rest mode:
 *   /data/etaHEN/lockall_state.txt   ("1" locked, "0" unlocked)
 * Token:
 *   /data/etaHEN/lockall.ini         ("token=changeme")
 *
 * Enforcement uses the same clean close the system uses for "Close
 * Application": sceSystemServiceKillApp(appid, -1, 0, 0). On PS5 the
 * GetAppIdOfRunningBigApp()/GetAppTitleId() pair is callable from a daemon, so
 * no per-tag hack is needed (the PS4 version had to use sceKernelGetAppInfo).
 */

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

#include <ps5/klog.h>

#define STATE_PATH    "/data/etaHEN/lockall_state.txt"
#define TOKEN_PATH    "/data/etaHEN/lockall.ini"
#define LOCK_PORT     9095
#define DEFAULT_TOKEN "changeme"
#define MAX_CMD       128

/* ---- system service (libSceSystemService) ---------------------------- */
extern int sceSystemServiceGetAppIdOfRunningBigApp(void);
extern int sceSystemServiceGetAppTitleId(int app_id, char *title_id);
extern int sceSystemServiceKillApp(int app_id, int opt, int method, int reason);

/* ---- notification (libkernel) ---------------------------------------- */
typedef struct notify_request {
    char useless1[45];
    char message[3075];
} notify_request_t;

extern int sceKernelSendNotificationRequest(int device, notify_request_t *req,
                                            size_t size, int blocking);

static volatile int g_running = 1;
static char         g_token[64] = DEFAULT_TOKEN;

/* ---------------------------------------------------------------------- */
/* logging / notification                                                */
/* ---------------------------------------------------------------------- */
static void logf_(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    klog_printf("[lockall] %s", buf);
}

static void notify(const char *msg) {
    notify_request_t req;
    memset(&req, 0, sizeof(req));
    snprintf(req.message, sizeof(req.message), "%s", msg);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

/* ---------------------------------------------------------------------- */
/* tiny file helpers (POSIX, safe inside an etaHEN daemon)               */
/* ---------------------------------------------------------------------- */
static int read_file(const char *path, char *buf, size_t buflen) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, buflen - 1);
    close(fd);
    if (n < 0) return -1;
    buf[n] = '\0';
    return (int)n;
}

static int load_locked(void) {
    char buf[16];
    if (read_file(STATE_PATH, buf, sizeof(buf)) < 0) return 0;
    return buf[0] == '1' ? 1 : 0;
}

static void save_locked(int locked) {
    int fd = open(STATE_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0777);
    if (fd < 0) {
        logf_("cannot write state file %s\n", STATE_PATH);
        return;
    }
    char b[2] = { locked ? '1' : '0', '\n' };
    write(fd, b, 2);
    close(fd);
}

static void load_token(void) {
    char buf[256];
    if (read_file(TOKEN_PATH, buf, sizeof(buf)) <= 0) return;

    char *p = strstr(buf, "token");
    if (!p) return;
    p = strchr(p, '=');
    if (!p) return;
    p++;
    while (*p == ' ' || *p == '\t') p++;

    char *end = p;
    while (*end && *end != '\r' && *end != '\n' && *end != ' ' && *end != '\t')
        end++;

    size_t len = (size_t)(end - p);
    if (len == 0 || len >= sizeof(g_token)) return;
    memcpy(g_token, p, len);
    g_token[len] = '\0';
    logf_("token loaded from %s\n", TOKEN_PATH);
}

/* ---------------------------------------------------------------------- */
/* which title ids count as a game                                       */
/* ---------------------------------------------------------------------- */
static int is_game_title(const char *tid) {
    static const char *prefixes[] = {
        "CUSA", "PPSA", "PCAS", "PCSA", "SCUS", "PPSP", "PPSX"
    };
    if (!tid || !tid[0]) return 0;
    for (size_t i = 0; i < sizeof(prefixes) / sizeof(prefixes[0]); i++) {
        if (strncmp(tid, prefixes[i], 4) == 0) return 1;
    }
    return 0;
}

/* ---------------------------------------------------------------------- */
/* enforcement: close the running game                                   */
/* ---------------------------------------------------------------------- */
static void *monitor_thread(void *arg) {
    (void)arg;
    int notified_appid = -1;

    while (g_running) {
        int appid = sceSystemServiceGetAppIdOfRunningBigApp();

        if (appid > 0) {
            char tid[32];
            memset(tid, 0, sizeof(tid));

            if (sceSystemServiceGetAppTitleId(appid, tid) == 0 &&
                is_game_title(tid) && load_locked()) {

                if (appid != notified_appid) {
                    notify("Games are locked");
                    notified_appid = appid;
                }

                int ret = sceSystemServiceKillApp(appid, -1, 0, 0);
                logf_("close app=0x%08x tid=%s ret=0x%08x\n", appid, tid, ret);

                /* the title normally disappears here; if it survived, do not
                 * hammer the system */
                usleep(1000 * 1000);
                continue;
            }
        } else {
            notified_appid = -1;
        }

        usleep(250 * 1000);
    }
    return NULL;
}

/* ---------------------------------------------------------------------- */
/* TCP server                                                            */
/* ---------------------------------------------------------------------- */
static int send_all(int fd, const char *s) {
    size_t len = strlen(s);
    size_t off = 0;
    while (off < len) {
        ssize_t n = send(fd, s + off, len - off, 0);
        if (n <= 0) return -1;
        off += (size_t)n;
    }
    return 0;
}

static void handle_client(int fd) {
    char buf[MAX_CMD + 1];
    size_t used = 0;

    /* read until the newline the client sends (a single recv may be
     * fragmented) */
    while (used < MAX_CMD) {
        int n = (int)recv(fd, buf + used, MAX_CMD - used, 0);
        if (n <= 0) break;
        used += (size_t)n;
        if (memchr(buf, '\n', used) || memchr(buf, '\r', used)) break;
    }
    if (used == 0) return;
    buf[used] = '\0';

    char *nl = strpbrk(buf, "\r\n");
    if (nl) *nl = '\0';

    char *colon = strchr(buf, ':');
    if (!colon) {
        send_all(fd, "ERR");
        return;
    }
    *colon = '\0';
    const char *token = buf;
    const char *cmd   = colon + 1;

    if (strcmp(token, g_token) != 0) {
        send_all(fd, "ERR");
        return;
    }

    if (strcmp(cmd, "lock") == 0) {
        save_locked(1);
        notify("Games are locked");
        logf_("locked via TCP\n");
        send_all(fd, "OK locked");
    } else if (strcmp(cmd, "unlock") == 0) {
        save_locked(0);
        logf_("unlocked via TCP\n");
        send_all(fd, "OK unlocked");
    } else if (strcmp(cmd, "status") == 0) {
        send_all(fd, load_locked() ? "locked" : "unlocked");
    } else {
        send_all(fd, "ERR");
    }
}

static void *server_thread(void *arg) {
    (void)arg;

    int srv = -1;
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(LOCK_PORT);

    /* etaHEN can load plugins very early: keep retrying until the network
     * stack and /data are ready instead of silently dying. */
    for (;;) {
        srv = socket(AF_INET, SOCK_STREAM, 0);
        if (srv < 0) {
            logf_("socket() failed, retrying\n");
            sleep(1);
            continue;
        }

        int one = 1;
        setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

        if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            logf_("bind(%d) failed, retrying\n", LOCK_PORT);
            close(srv);
            sleep(1);
            continue;
        }
        if (listen(srv, 4) < 0) {
            logf_("listen() failed, retrying\n");
            close(srv);
            sleep(1);
            continue;
        }
        break;
    }

    logf_("listening on port %d\n", LOCK_PORT);

    while (g_running) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) {
            usleep(100 * 1000);
            continue;
        }

        /* never let a half-open client block the control channel forever */
        struct timeval tv;
        tv.tv_sec  = 2;
        tv.tv_usec = 0;
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        handle_client(c);
        close(c);
    }

    close(srv);
    return NULL;
}

/* ---------------------------------------------------------------------- */
/* lifecycle                                                             */
/* ---------------------------------------------------------------------- */
static void on_signal(int sig) {
    logf_("received signal %d, stopping\n", sig);
    g_running = 0;
}

int main(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    mkdir("/data/etaHEN", 0777);

    load_token();

    /* make sure the state file exists so a fresh install is well-defined */
    if (access(STATE_PATH, F_OK) != 0) {
        save_locked(0);
    }

    logf_("ps5 lockall plugin starting (port %d, token set: %s)\n",
          LOCK_PORT, g_token);

    pthread_t srv, mon;
    if (pthread_create(&srv, NULL, server_thread, NULL) != 0) {
        logf_("failed to create server thread\n");
        return -1;
    }
    if (pthread_create(&mon, NULL, monitor_thread, NULL) != 0) {
        logf_("failed to create monitor thread\n");
        g_running = 0;
        pthread_join(srv, NULL);
        return -1;
    }

    pthread_join(srv, NULL);
    pthread_join(mon, NULL);
    return 0;
}
