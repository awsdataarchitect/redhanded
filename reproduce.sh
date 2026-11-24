#!/bin/bash
# Reproduce the trial runs from results/pr_refs.txt. Usage: ./reproduce.sh WORKDIR [repo-name]
# Needs network for cloning and pip (wheels only). Tests themselves run with no network.
set -e
HERE=$(cd "$(dirname "$0")" && pwd); W=${1:?workdir}; ONLY=$2; mkdir -p "$W/repos" "$W/venvs" "$W/out"
declare -A URL=([click]=pallets/click [more-itertools]=more-itertools/more-itertools [dateutil]=dateutil/dateutil [python-tabulate]=astanin/python-tabulate)
declare -A DEPS=([click]="" [more-itertools]="" [dateutil]="six hypothesis freezegun attrs" [python-tabulate]="wcwidth")
while read r n b h how rest; do
  [ -n "$ONLY" ] && [ "$ONLY" != "$r" ] && continue
  [ -d "$W/repos/$r" ] || git clone -q --filter=blob:none "https://github.com/${URL[$r]}" "$W/repos/$r"
  git -C "$W/repos/$r" fetch -q origin "refs/pull/$n/head:refs/prs/$n" || true
  [ -d "$W/venvs/$r" ] || "$HERE/build_venv.sh" "$W/venvs/$r" ${DEPS[$r]}
  python3 "$HERE/catchcheck.py" --gitdir "$W/repos/$r" --base "$b" --head "$h" --venv "$W/venvs/$r" --out "$W/out/$r-$n" --label "$r#$n"
done < "$HERE/results/pr_refs.txt"
