#!/usr/bin/env bash
# Install the WSL wrappers (gcloud, bq) into ~/.local/bin and an Ansible venv.
# Idempotent. Run from WSL Ubuntu:
#   bash /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/wsl/install.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin"
for tool in gcloud bq; do
  # strip CR in case git checked the file out with CRLF
  tr -d '\r' < "$here/$tool" > "$HOME/.local/bin/$tool"
  chmod +x "$HOME/.local/bin/$tool"
done
if [ ! -x "$HOME/.venvs/ansible/bin/ansible-playbook" ]; then
  mkdir -p "$HOME/.venvs"
  python3 -m venv "$HOME/.venvs/ansible"
  "$HOME/.venvs/ansible/bin/pip" install -q --upgrade pip
  "$HOME/.venvs/ansible/bin/pip" install -q -r <(tr -d '\r' < "$here/../ansible/requirements.txt")
fi
echo "wrappers: $HOME/.local/bin/gcloud $HOME/.local/bin/bq"
echo "ansible:  $HOME/.venvs/ansible/bin/ansible-playbook"
echo "PATH check: $(command -v gcloud)"
