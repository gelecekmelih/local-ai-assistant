#!/bin/bash
# kali_agent/start.sh — Kali Agent Otomatik Başlatıcı
# =====================================================
# WSL2 başladığında agent'ı arka planda çalıştırır.
#
# Kurulum (Kali terminali):
#   chmod +x /mnt/c/Users/youruser/jarvis/kali_agent/start.sh
#   echo "bash /mnt/c/Users/youruser/jarvis/kali_agent/start.sh" >> ~/.bashrc
#
# Elle başlatma:
#   bash /mnt/c/Users/youruser/jarvis/kali_agent/start.sh

AGENT="/mnt/c/Users/youruser/jarvis/kali_agent/agent.py"
LOG="/tmp/klargus_agent.log"
PORT=7474

# Zaten çalışıyor mu?
if curl -s -o /dev/null -w "%{http_code}" \
   -H "X-Token: klargus-kali-bridge" \
   "http://127.0.0.1:${PORT}/ping" 2>/dev/null | grep -q "200"; then
    echo "[KaliAgent] Zaten çalışıyor (port ${PORT})."
    exit 0
fi

echo "[KaliAgent] Başlatılıyor..."
nohup python3 "${AGENT}" > "${LOG}" 2>&1 &
echo "[KaliAgent] PID: $!  Log: ${LOG}"
sleep 1

# Doğrula
if curl -s -o /dev/null -w "%{http_code}" \
   -H "X-Token: klargus-kali-bridge" \
   "http://127.0.0.1:${PORT}/ping" 2>/dev/null | grep -q "200"; then
    echo "[KaliAgent] ✓ Aktif → http://127.0.0.1:${PORT}"
else
    echo "[KaliAgent] ✗ Başlatılamadı. Log: ${LOG}"
fi
