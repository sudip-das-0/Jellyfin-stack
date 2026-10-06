# systemd user units (templates)

Install or refresh after editing:

```bash
mkdir -p ~/.config/systemd/user
cp /mnt/jellyfin/scripts/systemd/user/*.service /mnt/jellyfin/scripts/systemd/user/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now media-stack.service
systemctl --user enable --now torrent-stall-recovery.timer download-activity-log.timer
systemctl --user enable --now lg-tv-optimize.service lg-tv-new-media-watch.service
```

`lg-tv-optimize.timer` is optional if you use the continuous `lg-tv-optimize.service` worker only.
