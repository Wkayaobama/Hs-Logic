#!/usr/bin/env bash
# Local verification of the deploy tooling from WSL (no GCP mutation):
#   wrappers on PATH, playbook syntax, .env parser proof (on .env.example only),
#   read-only preflight against the target project.
# Run from a login shell:  bash deploy/wsl/check.sh
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ansible_dir="$here/../ansible"
A="$HOME/.venvs/ansible/bin"
rc=0

echo "== wrappers =="
echo "gcloud -> $(command -v gcloud)"
echo "bq     -> $(command -v bq)"
gcloud config list --format='value(core.account)' 2>/dev/null | sed 's/^/account: /'
bq version 2>/dev/null | head -1

echo "== playbook syntax =="
cd "$ansible_dir" || exit 1
for p in foundation.yml deploy.yml verify.yml; do
  if "$A/ansible-playbook" --syntax-check "$p" >/dev/null 2>&1; then
    echo "syntax ok: $p"
  else
    echo "SYNTAX FAIL: $p"; "$A/ansible-playbook" --syntax-check "$p"; rc=1
  fi
done

echo "== .env parser proof (.env.example, never the real file) =="
out="$("$A/ansible" localhost -m debug -a 'msg={{ lookup("ansible.builtin.ini", "HUBSPOT_PORTAL_ID", type="properties", file="../../.env.example") }}' 2>&1)"
if grep -q '"msg": "9201667"' <<<"$out"; then
  echo "ini lookup ok: HUBSPOT_PORTAL_ID=9201667"
else
  echo "INI LOOKUP FAIL:"; echo "$out"; rc=1
fi

echo "== preflight (read-only) =="
if "$A/ansible-playbook" foundation.yml --tags preflight 2>&1 | grep -E 'account=|failed=' ; then :; fi
"$A/ansible-playbook" foundation.yml --tags preflight >/dev/null 2>&1 || { echo "PREFLIGHT FAIL"; rc=1; }

echo "== result: $([ $rc -eq 0 ] && echo OK || echo FAIL) =="
exit $rc
