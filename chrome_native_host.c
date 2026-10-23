#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_MESSAGE_BYTES (64U * 1024U)
#define APP_PATH_FILE_NAME "slideflow-app-path.txt"

enum ForwardResult {
    FORWARD_OK = 0,
    FORWARD_CONNECT_FAILED = 1,
    FORWARD_IO_FAILED = 2,
};

static int read_exact(int descriptor, void *buffer, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        ssize_t count = read(descriptor, (char *)buffer + offset, length - offset);
        if (count == 0) {
            return offset == 0 ? 0 : -1;
        }
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        offset += (size_t)count;
    }
    return 1;
}

static int write_all(int descriptor, const void *buffer, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        ssize_t count = write(descriptor, (const char *)buffer + offset, length - offset);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        offset += (size_t)count;
    }
    return 0;
}

static int write_native_message(const char *payload, uint32_t length) {
    unsigned char header[4] = {
        (unsigned char)(length & 0xffU),
        (unsigned char)((length >> 8U) & 0xffU),
        (unsigned char)((length >> 16U) & 0xffU),
        (unsigned char)((length >> 24U) & 0xffU),
    };
    if (write_all(STDOUT_FILENO, header, sizeof(header)) != 0 ||
        write_all(STDOUT_FILENO, payload, length) != 0) {
        return -1;
    }
    return 0;
}

static int write_error(const char *message) {
    char payload[512];
    int length = snprintf(
        payload,
        sizeof(payload),
        "{\"ok\":false,\"error\":\"%s\"}",
        message
    );
    if (length < 0 || (size_t)length >= sizeof(payload)) {
        return -1;
    }
    return write_native_message(payload, (uint32_t)length);
}

static int socket_path(char *output, size_t output_size) {
    const char *override = getenv("SLIDEFLOW_SOCKET_PATH");
    if (override != NULL && override[0] != '\0') {
        if (snprintf(output, output_size, "%s", override) >= (int)output_size) {
            return -1;
        }
        return 0;
    }
    struct passwd *account = getpwuid(getuid());
    if (account == NULL || account->pw_dir == NULL) {
        return -1;
    }
    int length = snprintf(
        output,
        output_size,
        "%s/Library/Application Support/SlideFlow/slideflow.sock",
        account->pw_dir
    );
    return length < 0 || (size_t)length >= output_size ? -1 : 0;
}

static enum ForwardResult forward_message(
    const char *path,
    const char *request,
    uint32_t request_length,
    char *response,
    uint32_t *response_length
) {
    int connection = socket(AF_UNIX, SOCK_STREAM, 0);
    if (connection < 0) {
        return FORWARD_CONNECT_FAILED;
    }
    struct timeval timeout = {.tv_sec = 4, .tv_usec = 0};
    (void)setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    (void)setsockopt(connection, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (strlen(path) >= sizeof(address.sun_path)) {
        close(connection);
        return FORWARD_CONNECT_FAILED;
    }
    memcpy(address.sun_path, path, strlen(path) + 1U);
    if (connect(connection, (struct sockaddr *)&address, sizeof(address)) != 0) {
        close(connection);
        return FORWARD_CONNECT_FAILED;
    }
    if (write_all(connection, request, request_length) != 0 ||
        write_all(connection, "\n", 1U) != 0) {
        close(connection);
        return FORWARD_IO_FAILED;
    }

    uint32_t length = 0;
    while (length < MAX_MESSAGE_BYTES) {
        char character;
        ssize_t count = read(connection, &character, 1U);
        if (count == 0) {
            break;
        }
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            close(connection);
            return FORWARD_IO_FAILED;
        }
        if (character == '\n') {
            break;
        }
        response[length++] = character;
    }
    close(connection);
    if (length == 0 || length >= MAX_MESSAGE_BYTES) {
        return FORWARD_IO_FAILED;
    }
    *response_length = length;
    return FORWARD_OK;
}

static int adjacent_app_path(char *output, size_t output_size) {
    char executable[PATH_MAX];
    uint32_t executable_size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &executable_size) != 0) {
        return -1;
    }
    char resolved[PATH_MAX];
    if (realpath(executable, resolved) == NULL) {
        return -1;
    }
    char *separator = strrchr(resolved, '/');
    if (separator == NULL) {
        return -1;
    }
    *separator = '\0';

    char config_path[PATH_MAX];
    int config_length = snprintf(
        config_path,
        sizeof(config_path),
        "%s/%s",
        resolved,
        APP_PATH_FILE_NAME
    );
    if (config_length < 0 || (size_t)config_length >= sizeof(config_path)) {
        return -1;
    }
    FILE *config = fopen(config_path, "r");
    if (config == NULL) {
        return -1;
    }
    if (fgets(output, (int)output_size, config) == NULL) {
        fclose(config);
        return -1;
    }
    fclose(config);
    output[strcspn(output, "\r\n")] = '\0';
    struct stat app_status;
    if (output[0] == '\0' || stat(output, &app_status) != 0 || !S_ISDIR(app_status.st_mode)) {
        return -1;
    }
    return 0;
}

static int open_slideflow(void) {
    char app_path[PATH_MAX];
    if (adjacent_app_path(app_path, sizeof(app_path)) != 0) {
        return -1;
    }
    pid_t child = fork();
    if (child < 0) {
        return -1;
    }
    if (child == 0) {
        execl("/usr/bin/open", "open", "-g", app_path, (char *)NULL);
        _exit(127);
    }
    int status = 0;
    if (waitpid(child, &status, 0) < 0) {
        return -1;
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    unsigned char header[4];
    int header_result = read_exact(STDIN_FILENO, header, sizeof(header));
    if (header_result == 0) {
        return 0;
    }
    if (header_result < 0) {
        (void)write_error("Native Messaging header is incomplete.");
        return 0;
    }
    uint32_t request_length =
        (uint32_t)header[0] |
        ((uint32_t)header[1] << 8U) |
        ((uint32_t)header[2] << 16U) |
        ((uint32_t)header[3] << 24U);
    if (request_length == 0 || request_length > MAX_MESSAGE_BYTES) {
        (void)write_error("Native Messaging message is too large.");
        return 0;
    }

    char *request = malloc(request_length);
    char *response = malloc(MAX_MESSAGE_BYTES);
    if (request == NULL || response == NULL) {
        free(request);
        free(response);
        (void)write_error("Native Messaging host is out of memory.");
        return 0;
    }
    if (read_exact(STDIN_FILENO, request, request_length) != 1) {
        free(request);
        free(response);
        (void)write_error("Native Messaging message is incomplete.");
        return 0;
    }

    char path[sizeof(((struct sockaddr_un *)0)->sun_path)];
    if (socket_path(path, sizeof(path)) != 0) {
        free(request);
        free(response);
        (void)write_error("SlideFlow socket path is unavailable.");
        return 0;
    }
    uint32_t response_length = 0;
    enum ForwardResult result = forward_message(
        path,
        request,
        request_length,
        response,
        &response_length
    );
    if (result == FORWARD_CONNECT_FAILED && open_slideflow() == 0) {
        for (int attempt = 0; attempt < 40; ++attempt) {
            usleep(200000U);
            result = forward_message(
                path,
                request,
                request_length,
                response,
                &response_length
            );
            if (result != FORWARD_CONNECT_FAILED) {
                break;
            }
        }
    }

    free(request);
    if (result == FORWARD_OK) {
        (void)write_native_message(response, response_length);
    } else if (result == FORWARD_CONNECT_FAILED) {
        (void)write_error("Cannot connect to SlideFlow. Open the app and try again.");
    } else {
        (void)write_error("SlideFlow did not return a valid response.");
    }
    free(response);
    return 0;
}
