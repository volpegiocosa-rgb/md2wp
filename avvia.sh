#!/bin/bash
# Avvia il server locale e apre il browser su http://localhost:5000
cd "$(dirname "$0")"

.venv/bin/python app.py &
SERVER_PID=$!

sleep 1

if command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:5000
elif command -v open >/dev/null 2>&1; then
    open http://localhost:5000
else
    echo "Apri manualmente http://localhost:5000 nel browser"
fi

wait $SERVER_PID
