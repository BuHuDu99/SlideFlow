#!/bin/sh
# Invoked only after the Python updater verifies the release and stages it.
set -eu
old_pid="$1"
app_path="$2"
stage="$3"
backup="$stage/previous.app"
error_file="$HOME/Library/Application Support/SlideFlow/update-error.txt"
restore() {
    if [ -d "$backup" ] && [ ! -e "$app_path" ]; then
        mv "$backup" "$app_path" || true
    fi
    mkdir -p "$(dirname "$error_file")"
    printf '%s\n' 'Không cài được bản cập nhật. SlideFlow đã giữ lại bản trước. Hãy kiểm tra quyền ghi và thử lại.' > "$error_file"
    /usr/bin/open "$app_path" || true
    # Preserve the backup if restoring failed.
    if [ ! -d "$backup" ]; then rm -rf "$stage"; fi
    exit 1
}
trap restore HUP INT TERM
# Bounded wait: never replace an application which is still running.
i=0
while kill -0 "$old_pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        rm -rf "$stage"
        exit 1
    fi
    sleep 1
done
mv "$app_path" "$backup" || restore
mv "$stage/SlideFlow.app" "$app_path" || restore
if ! /usr/bin/open "$app_path"; then
    mv "$app_path" "$stage/failed.app" || true
    restore
fi
rm -f "$error_file"
rm -rf "$stage"
