#!/bin/bash
#Deploy satellite backend to LunkVPS
#Usage, run deploy dot sh

set -e

VPS_HOST="<TAILSCALE_IP>"
REMOTE_DIR="/home/lunkman/Documents/LunkserverManager/satellite-backend"

echo "🚀 Deploying LunkServer Satellite Backend to LunkVPS..."

#Ensure remote repo dir exists
ssh -o StrictHostKeyChecking=no root@$VPS_HOST "mkdir -p $REMOTE_DIR"

#Sync code, exclude venv cache and git
rsync -avz --delete \
    --exclude=".git" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".venv" \
    ./ \
    root@$VPS_HOST:$REMOTE_DIR/

#Create venv as lunkman, install deps, install and restart systemd service
ssh root@$VPS_HOST bash -s << EOF
set -e
cd $REMOTE_DIR
sudo -u lunkman python3 -m venv .venv
sudo -u lunkman .venv/bin/pip install --upgrade pip
sudo -u lunkman .venv/bin/pip install -r requirements.txt
cp satellite.service /etc/systemd/system/satellite.service
systemctl daemon-reload
systemctl restart satellite
systemctl enable satellite
EOF

echo "✅ Deployment complete! Service running at http://$VPS_HOST:8765"
