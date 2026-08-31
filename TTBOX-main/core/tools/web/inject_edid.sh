#!/bin/bash
# Inject TTBox 1080p240 EDID into /dev/video0 at boot
sleep 5
DEV=/dev/video0
if [ -e "$DEV" ]; then
    v4l2-ctl -d "$DEV" --set-edid=file=/opt/ttbox/edid/ttbox_1080p240.hex 2>/dev/null
    echo "EDID injected: $(date)" >> /opt/ttbox/edid/inject.log
fi
exit 0
