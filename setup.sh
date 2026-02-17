#!/bin/bash
# Watchdog setup script — run on watchdog-server after deploying files.
set -e

WATCHDOG_DIR="/home/fabian/watchdog"

echo "=== Installing system dependencies ==="
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git jq curl

echo "=== Creating Python virtual environment ==="
python3 -m venv "$WATCHDOG_DIR/venv"
source "$WATCHDOG_DIR/venv/bin/activate"

echo "=== Installing Python packages ==="
pip install -r "$WATCHDOG_DIR/requirements.txt"

echo "=== Creating logs directory ==="
mkdir -p "$WATCHDOG_DIR/logs"

echo "=== Setting up SSH key for inter-VM access ==="
if [ ! -f ~/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "watchdog@watchdog-server"
    echo ""
    echo ">>> PUBLIC KEY (add this to openclaw-server's ~/.ssh/authorized_keys):"
    echo ""
    cat ~/.ssh/id_ed25519.pub
    echo ""
else
    echo "SSH key already exists."
    cat ~/.ssh/id_ed25519.pub
fi

echo "=== Setting up cron job (every 5 minutes) ==="
CRON_LINE="*/5 * * * * $WATCHDOG_DIR/venv/bin/python $WATCHDOG_DIR/watchdog.py >> $WATCHDOG_DIR/logs/cron.log 2>&1"
(crontab -l 2>/dev/null | grep -v "watchdog.py"; echo "$CRON_LINE") | crontab -
echo "Cron job installed."

echo "=== Setting up log rotation ==="
sudo tee /etc/logrotate.d/watchdog > /dev/null <<'LOGROTATE'
/home/fabian/watchdog/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
LOGROTATE
echo "Log rotation configured."

echo "=== Cloning OpenClaw repo ==="
if [ ! -d "$WATCHDOG_DIR/openclaw-repo" ]; then
    git clone https://github.com/openclaw/openclaw.git "$WATCHDOG_DIR/openclaw-repo" || echo "Clone failed — configure GitHub PAT in .env and clone manually"
else
    echo "Repo already cloned."
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Copy .env.example to .env and fill in your API keys:"
echo "     cp $WATCHDOG_DIR/.env.example $WATCHDOG_DIR/.env"
echo "     nano $WATCHDOG_DIR/.env"
echo ""
echo "  2. Add the SSH public key above to openclaw-server:"
echo "     ssh fabian@136.117.149.33 'cat >> ~/.ssh/authorized_keys' < ~/.ssh/id_ed25519.pub"
echo ""
echo "  3. Test the watchdog manually:"
echo "     source $WATCHDOG_DIR/venv/bin/activate"
echo "     python $WATCHDOG_DIR/watchdog.py"
echo ""
