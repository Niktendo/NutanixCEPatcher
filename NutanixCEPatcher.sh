#!/bin/bash

set -euo pipefail

BASE_DIR="/home/nutanix/foundation"
BACKUP_DIR="$BASE_DIR/backup_foundation"
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

declare -A SOURCES
SOURCES=(
    ["generate_iso"]="$SCRIPT_DIR/generate_iso"
    ["gui.py"]="$SCRIPT_DIR/gui.py"
    ["install.sh"]="$SCRIPT_DIR/install.sh"
    ["grub.cfg"]="$SCRIPT_DIR/grub.cfg"
    ["isolinux.cfg"]="$SCRIPT_DIR/isolinux.cfg"
)

declare -A TARGETS
TARGETS=(
    ["generate_iso"]="$BASE_DIR/bin/generate_iso"
    ["gui.py"]="$BASE_DIR/lib/phoenix/x86_64/gui.py"
    ["install.sh"]="$BASE_DIR/lib/phoenix/x86_64/install.sh"
    ["grub.cfg"]="$BASE_DIR/lib/phoenix/x86_64/EFI/BOOT/grub.cfg"
    ["isolinux.cfg"]="$BASE_DIR/lib/phoenix/x86_64/boot/isolinux/isolinux.cfg"
)

if [ -d "$BACKUP_DIR" ]; then
    
    echo "Backup directory found. Restoring..."
    
    for key in "${!TARGETS[@]}"; do
        TARGET_FILE="${TARGETS[$key]}"
        BACKUP_FILE="$BACKUP_DIR/$key.bak"

        if [ -f "$BACKUP_FILE" ]; then
            echo "  -> Restoring $TARGET_FILE"
            mv "$BACKUP_FILE" "$TARGET_FILE"
        else
            echo "  -> Removing $TARGET_FILE"
            rm "$TARGET_FILE"
        fi
    done
    
    rmdir "$BACKUP_DIR"
    echo "Restore completed. Run generate_iso as usual."

else
    
    echo "Backup directory not found. Backing up and copying new files..."
    
    for key in "${!SOURCES[@]}"; do
        if [ ! -f "${SOURCES[$key]}" ]; then
            echo "ERROR: Source file ${SOURCES[$key]} not found."
            exit 1
        fi
    done

    mkdir -p "$BACKUP_DIR"
    
    for key in "${!TARGETS[@]}"; do
        TARGET_FILE="${TARGETS[$key]}"
        SOURCE_FILE="${SOURCES[$key]}"
        BACKUP_FILE="$BACKUP_DIR/$key.bak"

        if [ -f "$TARGET_FILE" ]; then
            echo "  -> Backing up $TARGET_FILE..."
            cp "$TARGET_FILE" "$BACKUP_FILE"
        fi

        echo "  -> Copying $SOURCE_FILE to $TARGET_FILE"
        mkdir -p "$(dirname "$TARGET_FILE")"
        cp "$SOURCE_FILE" "$TARGET_FILE"
    done
    
    echo "Patching completed. Run generate_iso as usual."
    
fi