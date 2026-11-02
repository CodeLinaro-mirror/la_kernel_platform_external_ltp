#!/bin/bash
# Entry point for external_updater post-update hook.
# Handles both git merge (no backup path) and archive update (with backup path).
#
# $1: new_project_path
# $2: old_project_path (optional, present in archive/tarball update mode)

NEW_PATH="$1"
OLD_PATH="$2"

if [ -z "$NEW_PATH" ]; then
  echo "Error: new_project_path is required."
  exit 1
fi

# 1. Restore AOSP-specific android/ directory and custom runlists if we are in archive update mode
if [ -n "$OLD_PATH" ]; then
  echo "Restoring android/ directory from $OLD_PATH to $NEW_PATH..."
  if [ -d "$OLD_PATH/android" ]; then
    # Copy the android directory recursively
    mkdir -p "$NEW_PATH"
    cp -r "$OLD_PATH/android" "$NEW_PATH/"
    echo "android/ directory restored."
  else
    echo "Error: Source android/ directory not found in $OLD_PATH"
    exit 1
  fi

  # Restore custom runlists
  CUSTOM_RUNLISTS=("fcntl-locktests_android" "sched_low_mem")
  for runlist in "${CUSTOM_RUNLISTS[@]}"; do
    src_runlist="$OLD_PATH/runtest/$runlist"
    dst_runlist="$NEW_PATH/runtest/$runlist"
    if [ -f "$src_runlist" ]; then
      echo "Restoring custom runlist: $runlist..."
      mkdir -p "$(dirname "$dst_runlist")"
      cp "$src_runlist" "$dst_runlist"
    fi
  done
else
  echo "Git merge mode detected. android/ directory and runlists should be preserved."
fi

# 2. Attempt to regenerate Android blueprints
GEN_SH="$NEW_PATH/android/tools/gen_android_build.sh"

if [ -f "$GEN_SH" ]; then
  echo "Triggering Android build blueprint regeneration..."
  # Run the generator in its directory, and don't fail the post_update if it fails
  (cd "$NEW_PATH/android/tools" && NON_INTERACTIVE=1 ./gen_android_build.sh --update </dev/null) || {
    echo "========================================================================"
    echo "WARNING: Failed to automatically regenerate Android blueprints."
    echo "This usually happens if Docker is not available or lacks permissions."
    echo "Please run the generator manually to update blueprints:"
    echo "  cd android/tools && ./gen_android_build.sh --update"
    echo "========================================================================"
  }
else
  echo "Warning: Blueprint generator script not found at $GEN_SH"
fi

# 3. Generate detailed test comparison summary and update commit message via git commit --amend
echo "Generating test comparison summary and updating commit message..."

cd "$NEW_PATH"

# Extract a field of the git identifier from METADATA
metadata_field() {
  grep -E "^[[:space:]]*$1:" METADATA 2>/dev/null | head -n1 | sed -E 's/.*"([^"]+)".*/\1/'
}

# Extract version from METADATA. For a tag (stable release) update,
# external_updater records the commit SHA in "version" and the tag in
# "closest_version", so prefer "closest_version" when it is present.
NEW_VER=$(metadata_field closest_version)
if [ -z "$NEW_VER" ]; then
  NEW_VER=$(metadata_field version)
fi
if [ -z "$NEW_VER" ]; then
  NEW_VER=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
fi

# Determine if this is a Tag update or a Commit SHA update
if [[ "$NEW_VER" =~ ^[0-9a-fA-F]{40}$ ]]; then
  CURR_DATE=$(date +%Y%m%d)
  TITLE="LTP $CURR_DATE update"
  TYPE_DESC="Upstream commit: https://github.com/linux-test-project/ltp/commit/$NEW_VER"
  VERSION_STR="$CURR_DATE"
else
  TITLE="LTP $NEW_VER release update"
  TYPE_DESC="Release note: https://github.com/linux-test-project/ltp/releases/tag/$NEW_VER"
  VERSION_STR="$NEW_VER"
fi

# Automatically update VERSION file and stage it
echo "Updating VERSION file to $VERSION_STR..."
echo "$VERSION_STR" > "$NEW_PATH/VERSION"
git add "$NEW_PATH/VERSION"


# Extract test comparison diff
TEST_DIFF=""
COMPARE_PY="$NEW_PATH/android/tools/compare_ltp_projects.py"
if [ -f "$COMPARE_PY" ]; then
  if [ -n "$OLD_PATH" ] && [ -d "$OLD_PATH" ]; then
    # Archive update: compare the saved old tree with the new tree.
    TEST_DIFF=$(python3 "$COMPARE_PY" --ltp-old "$OLD_PATH" --ltp-new "$NEW_PATH" 2>/dev/null || true)
  else
    # Git merge: compare the previous commit with the updated working tree.
    TEST_DIFF=$(python3 "$COMPARE_PY" --git-repo "$NEW_PATH" --old-rev HEAD~1 --ltp-new "$NEW_PATH" 2>/dev/null || true)
  fi
fi

# Preserve existing footers from current commit message
ORIG_MSG=$(git log -1 --format=%B 2>/dev/null || true)
BUG_LINE=$(echo "$ORIG_MSG" | grep -E '^Bug:' | head -n1 || echo "Bug: None")
TEST_LINE=$(echo "$ORIG_MSG" | grep -E '^Test:' | head -n1 || echo "Test: TreeHugger")
# external_updater adds UPSTREAM_REV_ID, which Copybara/CaaS uses to find the
# starting point of the next update.
REV_ID_LINE=$(echo "$ORIG_MSG" | grep -E '^UPSTREAM_REV_ID:' | head -n1 || true)
CHANGE_ID=$(echo "$ORIG_MSG" | grep -E '^Change-Id:' | head -n1 || true)
if [ -z "$CHANGE_ID" ]; then
  CHANGE_ID="Change-Id: I$(python3 -c 'import secrets; print(secrets.token_hex(20))')"
fi
SIGNED_OFF=$(echo "$ORIG_MSG" | grep -E '^Signed-off-by:' | head -n1 || true)
if [ -z "$SIGNED_OFF" ]; then
  USER_NAME=$(git config user.name 2>/dev/null || echo "Wake Liu")
  USER_EMAIL=$(git config user.email 2>/dev/null || echo "wakel@google.com")
  SIGNED_OFF="Signed-off-by: $USER_NAME <$USER_EMAIL>"
fi

# Build and amend the commit message
NEW_COMMIT_FILE=$(mktemp)
cat <<EOF > "$NEW_COMMIT_FILE"
$TITLE

This update was generated automatically by external_updater.
$TYPE_DESC
EOF

if [ -n "$TEST_DIFF" ]; then
  echo "" >> "$NEW_COMMIT_FILE"
  echo "$TEST_DIFF" >> "$NEW_COMMIT_FILE"
fi

echo "" >> "$NEW_COMMIT_FILE"
if [ -n "$REV_ID_LINE" ]; then
  echo "$REV_ID_LINE" >> "$NEW_COMMIT_FILE"
fi
echo "$BUG_LINE" >> "$NEW_COMMIT_FILE"
echo "$TEST_LINE" >> "$NEW_COMMIT_FILE"
echo "$SIGNED_OFF" >> "$NEW_COMMIT_FILE"
echo "$CHANGE_ID" >> "$NEW_COMMIT_FILE"

# Stage updated files before amending the commit
git add "$NEW_PATH/VERSION" "$NEW_PATH/gen.bp" "$NEW_PATH/android/include/config.h" 2>/dev/null || true

git commit --amend -F "$NEW_COMMIT_FILE" || {
  echo "Warning: Failed to amend commit message."
}
rm -f "$NEW_COMMIT_FILE"
