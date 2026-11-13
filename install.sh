#!/usr/bin/env bash
# Installa CastVoice: ambiente Python, icona, voce nel menu e (su KDE) icona nella barra.
#   ./install.sh            installa
#   ./install.sh --pin-kde  installa e fissa l'icona sulla barra delle applicazioni di KDE Plasma
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "→ ambiente Python"
python3 -m venv --system-site-packages .venv
./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/python -c "import PySide6" 2>/dev/null || ./.venv/bin/pip install -q PySide6

echo "→ icona e voce nel menu"
for s in 16 32 48 64 128 256 512; do
  install -Dm644 "assets/icons/${s}x${s}.png" "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/castvoice.png"
done
install -Dm644 assets/castvoice.svg "$HOME/.local/share/icons/hicolor/scalable/apps/castvoice.svg"
mkdir -p "$HOME/.local/share/applications"
sed "s|@DIR@|$DIR|g" castvoice.desktop > "$HOME/.local/share/applications/castvoice.desktop"
chmod +x run.sh "$HOME/.local/share/applications/castvoice.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf "$HOME/.local/share/icons/hicolor" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

if [ "${1:-}" = "--pin-kde" ]; then
  echo "→ fisso l'icona sulla barra di KDE Plasma"
  qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '
    var id = "applications:castvoice.desktop";
    var ps = panels();
    for (var i = 0; i < ps.length; i++) {
      var ws = ps[i].widgets();
      for (var j = 0; j < ws.length; j++) {
        var w = ws[j];
        if (w.type == "org.kde.plasma.icontasks" || w.type == "org.kde.plasma.taskmanager") {
          w.currentConfigGroup = ["General"];
          var l = String(w.readConfig("launchers", "")).split(",").filter(function (x) { return x; });
          if (l.indexOf(id) < 0) { l.push(id); w.writeConfig("launchers", l.join(",")); }
        }
      }
    }'
fi

echo "✔ fatto: cerca «CastVoice» nel menu delle applicazioni."
