#!/usr/bin/env bash
# Apply Intel VAAPI-first HW transcode (encoding.xml + LG optimize systemd + verify).
# QSV remains as fallback in lg-tv-optimize-media.sh.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-paths.sh
source "${DIR}/../lib/stack-paths.sh"
ENC="${JELLYFIN_CONFIG_ROOT}/config/encoding.xml"
USER_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/lg-tv-optimize.service"
REPO_UNIT="$DIR/../systemd/user/lg-tv-optimize.service"

patch_encoding_xml() {
  [[ -f "$ENC" ]] || { echo "missing $ENC"; return 1; }
  python3 - "$ENC" <<'PY'
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])
tree = ET.parse(path)
root = tree.getroot()
ns = {"x": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

def find(tag):
    for el in root.iter():
        if el.tag.endswith(tag):
            return el
    return None

def set_text(tag, value):
    el = find(tag)
    if el is None:
        return
    el.text = value

set_text("HardwareAccelerationType", "vaapi")
set_text("VaapiDevice", "/dev/dri/renderD128")
set_text("QsvDevice", "/dev/dri/renderD128")
set_text("EnableHardwareEncoding", "true")
set_text("PreferSystemNativeHwDecoder", "true")

hw = find("HardwareDecodingCodecs")
if hw is not None:
    want = {"h264", "hevc", "av1", "vp9", "vc1"}
    have = {el.text for el in hw.findall("string") if el.text}
    for c in sorted(want - have):
        s = ET.SubElement(hw, "string")
        s.text = c

tree.write(path, encoding="utf-8", xml_declaration=True)
print("encoding.xml: VAAPI-first + HW decode codecs updated")
PY
}

install_optimize_unit() {
  mkdir -p "$(dirname "$USER_UNIT")"
  cp "$REPO_UNIT" "$USER_UNIT"
  echo "installed $USER_UNIT (5 GiB/h, 15M maxrate, LG_TV_AV1_HW_ATTEMPTS=3, LG_TV_OPTIMIZE_MAX_RETRIES=4)"
}

main() {
  patch_encoding_xml
  install_optimize_unit
  systemctl --user daemon-reload
  echo "systemd user daemon-reload done (restart lg-tv-optimize.service when idle to load new Environment=)"
  "$DIR/../stack-verify.sh" | sed -n '/Jellyfin transcode/,/Summary/p'
}

main "$@"
