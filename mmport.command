#!/bin/bash
# mmport — cift tiklanabilir baslatici (macOS)
#
# Bu dosyaya Finder'dan cift tiklayin. Tarayicida yerel arayuz acilir.
# Ilk acilista Gatekeeper sikayet ederse: Finder'da sag tik > Ac.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 bulunamadi. Once Xcode komut satiri araclarini kurun:"
  echo "  xcode-select --install"
  read -r -p "Kapatmak icin Enter..."
  exit 1
fi

echo "mmport baslatiliyor..."
python3 -m mmport --serve
read -r -p "Kapatmak icin Enter..."
