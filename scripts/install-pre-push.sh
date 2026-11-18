#!/bin/sh
# Adds `make ready` to the repository's pre-push hook, keeping any lines
# already in it.
set -eu
hook="$(git rev-parse --git-path hooks)/pre-push"
if [ -f "$hook" ] && grep -q 'make ready' "$hook"; then
  echo "pre-push already runs make ready"
elif [ -f "$hook" ]; then
  printf '\nmake ready\n' >> "$hook"
  echo "added make ready to $hook"
else
  printf '#!/bin/sh\nmake ready\n' > "$hook"
  chmod +x "$hook"
  echo "installed $hook"
fi
