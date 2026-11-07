#!/usr/bin/env bash
# Fail unless the jellyfin library SSD is mounted, writable, and passes a short I/O probe.
set -euo pipefail
MNT="${JELLYFIN_MNT:-/mnt/jellyfin}"
UUID_EXPECT="${JELLYFIN_UUID:-0caa73f3-4f57-4549-8e6d-fbf323866d28}"
MARKER="${MNT}/.jellyfin-external-drive"
PROBE_DIR="${JELLYFIN_PROBE_DIR:-${MNT}/torrents/incomplete}"
MIN_FREE_MB="${JELLYFIN_MIN_FREE_MB:-30720}"

die() {
  echo "ensure-jellyfin-media-mount: $*" >&2
  exit 1
}

mountpoint -q "$MNT" || die "$MNT is not mounted — plug in the jellyfin SSD"

opts=$(findmnt -no OPTIONS "$MNT" 2>/dev/null || true)
[[ "$opts" == *rw* ]] || die "$MNT is not read-write (options: ${opts:-unknown})"

actual=$(findmnt -no UUID "$MNT" 2>/dev/null || true)
[[ "$actual" == "$UUID_EXPECT" ]] || die "$MNT is mounted as UUID=$actual (expected $UUID_EXPECT)"

[[ -f "$MARKER" ]] || die "missing $MARKER (wrong disk or corrupt mount)"

[[ -d "$PROBE_DIR" ]] || die "$PROBE_DIR missing"
[[ -w "$PROBE_DIR" ]] || die "$PROBE_DIR not writable"

avail_mb=$(df -Pm "$MNT" 2>/dev/null | awk 'NR==2 {print $4}')
[[ -n "$avail_mb" && "$avail_mb" -ge "$MIN_FREE_MB" ]] \
  || die "low free space on $MNT (${avail_mb:-?} MiB free; need >= ${MIN_FREE_MB} MiB)"

root_dev=$(stat -c '%d' / 2>/dev/null) || die "cannot stat root"
mnt_dev=$(stat -c '%d' "$MNT" 2>/dev/null) || die "cannot stat $MNT"
[[ "$mnt_dev" != "$root_dev" ]] || die "$MNT is on the same block device as / (internal drive)"

probe="${PROBE_DIR}/.drive-health-probe"
if ! { echo "probe $(date +%s)" >"$probe" 2>/dev/null && sync -f "$probe" 2>/dev/null && test -r "$probe"; }; then
  die "I/O probe failed on $PROBE_DIR (SSD mount stale or failing)"
fi
rm -f "$probe" 2>/dev/null || true

echo "ok: jellyfin media on UUID=$actual at $MNT (${avail_mb} MiB free)"
