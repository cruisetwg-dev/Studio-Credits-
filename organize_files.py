#!/usr/bin/env python3
"""
===============================================================================
 organize_files.py
===============================================================================
A file-organization toolkit for Mac (also works on Windows/Linux since it's
plain Python).

WHAT THIS SCRIPT DOES
  1. Creates a standard folder structure (Documents, Photos, Videos, Taxes,
     School, Work, Music, Archives, Other)
  2. Analyzes a folder of loose files and SUGGESTS where each one should go
     (writes a CSV report - nothing is moved until you approve it)
  3. Renames files to a consistent "yyyy-MM-dd_OriginalName.ext" format
  4. Finds duplicate files (by content hash, not just filename) and reports
     which copies are safe to delete
  5. Actually moves/organizes files into the folder structure, based on
     extension + keyword rules you can edit near the top of this file
  6. Works on a local folder OR your OneDrive folder (auto-detected on Mac)

HOW TO USE WITH COPILOT / CLAUDE
  This script is written in plain, commented Python so an AI coding
  assistant can read it, explain any part, and help you customize the
  rules below. Good prompts to try:
    - "Explain what get_category_for_file does"
    - "Add a rule that sends files with 'invoice' in the name to Work"
    - "Change the rename format to put the date at the end instead"

SAFETY
  - Every destructive action (move, rename, delete) supports --dry-run.
  - Run with --dry-run FIRST to preview changes with zero risk.
  - Duplicates are only REPORTED by default - nothing is deleted
    automatically unless you pass --delete-duplicates.

HOW TO RUN
  1. Open Terminal, cd to the folder containing this script
  2. Run it interactively:
       python3 organize_files.py
  3. Or call it directly with arguments, e.g.:
       python3 organize_files.py init --root ~/Organized
       python3 organize_files.py plan --source ~/Downloads --root ~/Organized
       python3 organize_files.py organize --source ~/Downloads --root ~/Organized --dry-run
       python3 organize_files.py rename --path ~/Organized/Photos --dry-run
       python3 organize_files.py duplicates --path ~/Organized
       python3 organize_files.py onedrive --root ~/Organized --dry-run

  No extra packages needed - only the Python standard library.
===============================================================================
"""

import argparse
import csv
import hashlib
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------------
# 1. CONFIGURATION - edit these to fit how you want things sorted
# ------------------------------------------------------------------------

FOLDER_STRUCTURE = [
    "Documents",
    "Photos",
    "Videos",
    "Taxes",
    "School",
    "Work",
    "Music",
    "Archives",
    "Other",
]

# Extension -> category. Add/remove extensions freely (lowercase, with dot).
EXTENSION_MAP = {
    # Documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".txt": "Documents", ".rtf": "Documents", ".odt": "Documents",
    ".xls": "Documents", ".xlsx": "Documents", ".csv": "Documents",
    ".ppt": "Documents", ".pptx": "Documents", ".pages": "Documents",
    ".numbers": "Documents", ".key": "Documents",

    # Photos
    ".jpg": "Photos", ".jpeg": "Photos", ".png": "Photos",
    ".heic": "Photos", ".gif": "Photos", ".bmp": "Photos",
    ".tif": "Photos", ".tiff": "Photos", ".raw": "Photos",
    ".webp": "Photos",

    # Videos
    ".mp4": "Videos", ".mov": "Videos", ".avi": "Videos",
    ".mkv": "Videos", ".wmv": "Videos", ".m4v": "Videos",

    # Music
    ".mp3": "Music", ".wav": "Music", ".flac": "Music",
    ".aac": "Music", ".m4a": "Music", ".aiff": "Music",

    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives",
}

# Filename keywords (case-insensitive) that OVERRIDE the extension category.
# Checked first, so "W2-2025.pdf" goes to Taxes even though .pdf normally
# maps to Documents.
KEYWORD_MAP = {
    "Taxes":  ["tax", "1099", "w-2", "w2", "irs", "return", "schedule c", "1040"],
    "School": ["syllabus", "transcript", "homework", "assignment", "lecture",
               "tuition", "financial aid", "fafsa"],
    "Work":   ["invoice", "resume", "cv", "contract", "proposal",
               "timesheet", "payroll", "offer letter"],
}

INVALID_CHARS_RE = re.compile(r'[/:*?"<>|]')  # macOS mainly cares about "/" and ":"

# ------------------------------------------------------------------------
# 2. CREATE FOLDER STRUCTURE
# ------------------------------------------------------------------------
def initialize_folder_structure(root_path: Path, dry_run: bool = False):
    root_path = Path(root_path).expanduser()
    if not root_path.exists():
        print(f"{'[DRY RUN] Would create' if dry_run else 'Creating'} root: {root_path}")
        if not dry_run:
            root_path.mkdir(parents=True, exist_ok=True)

    for folder in FOLDER_STRUCTURE:
        full_path = root_path / folder
        if full_path.exists():
            print(f"Already exists: {full_path}")
        else:
            print(f"{'[DRY RUN] Would create' if dry_run else 'Created'}: {full_path}")
            if not dry_run:
                full_path.mkdir(parents=True, exist_ok=True)

    print(f"\nFolder structure ready at {root_path}")


# ------------------------------------------------------------------------
# 3. CATEGORIZATION LOGIC (used by both the planner and the mover)
# ------------------------------------------------------------------------
def get_category_for_file(file_path: Path) -> str:
    name_lower = file_path.name.lower()

    # Keyword rules win first
    for category, keywords in KEYWORD_MAP.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category

    # Fall back to extension rules
    ext = file_path.suffix.lower()
    return EXTENSION_MAP.get(ext, "Other")


# ------------------------------------------------------------------------
# 4. ANALYZE + SUGGEST (no files are moved - writes a CSV report)
# ------------------------------------------------------------------------
def get_organization_plan(source_path: Path, root_path: Path, report_path: Path = None):
    source_path = Path(source_path).expanduser()
    root_path = Path(root_path).expanduser()
    if not source_path.exists():
        print(f"ERROR: source path not found: {source_path}", file=sys.stderr)
        return []

    if report_path is None:
        report_path = root_path / "organization-plan.csv"
    else:
        report_path = Path(report_path).expanduser()

    files = [f for f in source_path.rglob("*") if f.is_file()]
    plan = []
    for f in files:
        category = get_category_for_file(f)
        plan.append({
            "CurrentPath": str(f),
            "FileName": f.name,
            "SizeMB": round(f.stat().st_size / (1024 * 1024), 2),
            "LastModified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(sep=" ", timespec="seconds"),
            "SuggestedFolder": category,
            "SuggestedPath": str(root_path / category / f.name),
        })

    root_path.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=list(plan[0].keys()) if plan else
                                 ["CurrentPath", "FileName", "SizeMB", "LastModified", "SuggestedFolder", "SuggestedPath"])
        writer.writeheader()
        writer.writerows(plan)

    print(f"\nAnalyzed {len(files)} files.")
    print(f"Suggestions written to: {report_path}")

    counts = defaultdict(int)
    for row in plan:
        counts[row["SuggestedFolder"]] += 1
    print("\nSummary by category:")
    for category, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {category:<12} {count}")

    return plan


# ------------------------------------------------------------------------
# 5. ACTUALLY ORGANIZE FILES (moves them - supports --dry-run)
# ------------------------------------------------------------------------
def invoke_file_organization(source_path: Path, root_path: Path, dry_run: bool = False, recurse: bool = True):
    source_path = Path(source_path).expanduser()
    root_path = Path(root_path).expanduser()

    initialize_folder_structure(root_path, dry_run=dry_run)

    files = [f for f in (source_path.rglob("*") if recurse else source_path.glob("*")) if f.is_file()]

    moved = 0
    for f in files:
        category = get_category_for_file(f)
        dest_folder = root_path / category
        dest_path = dest_folder / f.name

        # Avoid overwriting: append (1), (2), etc. if the name already exists
        counter = 1
        while dest_path.exists():
            dest_path = dest_folder / f"{f.stem} ({counter}){f.suffix}"
            counter += 1

        print(f"{'[DRY RUN] Would move' if dry_run else 'Moving'}: {f} -> {dest_path}")
        if not dry_run:
            dest_folder.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest_path))
            moved += 1

    if dry_run:
        print(f"\n[DRY RUN] Would organize {len(files)} file(s) into {root_path}")
    else:
        print(f"\nOrganized {moved} file(s) into {root_path}")


# ------------------------------------------------------------------------
# 6. RENAME FILES TO A CONSISTENT FORMAT: yyyy-MM-dd_OriginalName.ext
# ------------------------------------------------------------------------
def rename_files_to_standard(path: Path, dry_run: bool = False, recurse: bool = False, use_created_date: bool = False):
    path = Path(path).expanduser()
    files = [f for f in (path.rglob("*") if recurse else path.glob("*")) if f.is_file()]

    already_named_re = re.compile(r"^\d{4}-\d{2}-\d{2}_")
    renamed = 0

    for f in files:
        stat = f.stat()
        ts = stat.st_birthtime if (use_created_date and hasattr(stat, "st_birthtime")) else stat.st_mtime
        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

        clean_name = INVALID_CHARS_RE.sub("", f.stem)
        clean_name = re.sub(r"\s+", " ", clean_name).strip()

        # Don't re-stamp a file that's already in the standard format
        if already_named_re.match(clean_name):
            continue

        new_name = f"{date_str}_{clean_name}{f.suffix}"
        new_path = f.with_name(new_name)

        counter = 1
        while new_path.exists() and new_path != f:
            alt_name = f"{date_str}_{clean_name} ({counter}){f.suffix}"
            new_path = f.with_name(alt_name)
            counter += 1

        if new_path != f:
            print(f"{'[DRY RUN] Would rename' if dry_run else 'Renaming'}: {f.name} -> {new_path.name}")
            if not dry_run:
                f.rename(new_path)
                renamed += 1

    if dry_run:
        print(f"\n[DRY RUN] Would rename files in {path}")
    else:
        print(f"\nRenamed {renamed} file(s) in {path}")


# ------------------------------------------------------------------------
# 7. FIND DUPLICATES (by content hash - catches renamed copies too)
# ------------------------------------------------------------------------
def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate_files(path: Path, recurse: bool = True, report_path: Path = None,
                          delete_duplicates: bool = False, dry_run: bool = False):
    path = Path(path).expanduser()
    if report_path is None:
        report_path = path / "duplicate-report.csv"
    else:
        report_path = Path(report_path).expanduser()

    files = [f for f in (path.rglob("*") if recurse else path.glob("*")) if f.is_file()]
    print(f"Hashing {len(files)} files - this may take a moment...")

    by_hash = defaultdict(list)
    for f in files:
        try:
            digest = _hash_file(f)
            by_hash[digest].append(f)
        except (OSError, PermissionError) as e:
            print(f"WARNING: could not hash {f}: {e}", file=sys.stderr)

    dupe_groups = {h: paths for h, paths in by_hash.items() if len(paths) > 1}

    if not dupe_groups:
        print("No duplicates found.")
        return []

    report_rows = []
    to_delete = []
    for digest, paths in dupe_groups.items():
        sorted_paths = sorted(paths, key=lambda p: p.stat().st_mtime)
        keep = sorted_paths[0]
        for p in sorted_paths:
            is_keep = (p == keep)
            report_rows.append({
                "Hash": digest,
                "Path": str(p),
                "SizeMB": round(p.stat().st_size / (1024 * 1024), 2),
                "LastModified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(sep=" ", timespec="seconds"),
                "Recommendation": "KEEP (oldest copy)" if is_keep else "Duplicate - safe to delete",
            })
            if not is_keep:
                to_delete.append(p)

    with open(report_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["Hash", "Path", "SizeMB", "LastModified", "Recommendation"])
        writer.writeheader()
        writer.writerows(report_rows)

    wasted_mb = round(sum(p.stat().st_size for p in to_delete) / (1024 * 1024), 2)
    print(f"\nFound {len(dupe_groups)} sets of duplicates "
          f"({len(to_delete)} removable files, ~{wasted_mb} MB reclaimable)")
    print(f"Full report: {report_path}")

    if delete_duplicates:
        for p in to_delete:
            print(f"{'[DRY RUN] Would delete' if dry_run else 'Deleting'}: {p}")
            if not dry_run:
                p.unlink()
        if not dry_run:
            print("Duplicate files removed (oldest copy of each kept).")
    else:
        print(f"Nothing was deleted. Review {report_path}, then re-run with --delete-duplicates to clean up.")

    return report_rows


# ------------------------------------------------------------------------
# 8. ONEDRIVE HELPER (Mac) - finds your OneDrive folder automatically
# ------------------------------------------------------------------------
def get_onedrive_path() -> Path | None:
    home = Path.home()
    candidates = [
        home / "Library" / "CloudStorage",  # modern OneDrive location, contains "OneDrive-<org>" subfolders
        home / "OneDrive",
    ]

    # Check the modern CloudStorage location first for any OneDrive-* folder
    cloud_storage = home / "Library" / "CloudStorage"
    if cloud_storage.exists():
        matches = sorted(cloud_storage.glob("OneDrive*"))
        if matches:
            return matches[0]

    for candidate in candidates[1:]:
        if candidate.exists():
            return candidate

    print("WARNING: could not auto-detect OneDrive folder. "
          "Pass it manually with --source, e.g. --source ~/Library/CloudStorage/OneDrive-Personal",
          file=sys.stderr)
    return None


def invoke_onedrive_organization(root_path: Path, dry_run: bool = False, onedrive_path: Path = None):
    onedrive_path = onedrive_path or get_onedrive_path()
    if not onedrive_path:
        return
    print(f"Organizing OneDrive folder: {onedrive_path}")
    invoke_file_organization(onedrive_path, root_path, dry_run=dry_run)


# ------------------------------------------------------------------------
# 9. INTERACTIVE MENU (runs when the script is called with no arguments)
# ------------------------------------------------------------------------
def show_menu():
    while True:
        print("\n=======================================")
        print("   File Organizer")
        print("=======================================")
        print("1. Create folder structure")
        print("2. Analyze a folder + suggest sorting (CSV report, nothing moved)")
        print("3. Organize files into folder structure (preview first!)")
        print("4. Rename files to consistent format")
        print("5. Find duplicate files")
        print("6. Organize my OneDrive folder")
        print("0. Exit")
        choice = input("\nChoose an option: ").strip()

        if choice == "1":
            root = input("Where should the folder structure be created? (e.g. ~/Organized): ").strip()
            initialize_folder_structure(Path(root))

        elif choice == "2":
            src = input("Folder to analyze (e.g. ~/Downloads): ").strip()
            root = input("Root folder your suggestions should reference (e.g. ~/Organized): ").strip()
            get_organization_plan(Path(src), Path(root))

        elif choice == "3":
            src = input("Folder to organize FROM: ").strip()
            root = input("Root folder to organize INTO: ").strip()
            preview = input("Preview only first? (Y/N): ").strip()
            if preview.lower().startswith("y"):
                invoke_file_organization(Path(src), Path(root), dry_run=True)
                go = input("\nPreview looked good? Run for real? (Y/N): ").strip()
                if go.lower().startswith("y"):
                    invoke_file_organization(Path(src), Path(root), dry_run=False)
            else:
                invoke_file_organization(Path(src), Path(root), dry_run=False)

        elif choice == "4":
            path = input("Folder of files to rename: ").strip()
            rename_files_to_standard(Path(path), dry_run=True)
            go = input("\nPreview looked good? Run for real? (Y/N): ").strip()
            if go.lower().startswith("y"):
                rename_files_to_standard(Path(path), dry_run=False)

        elif choice == "5":
            path = input("Folder to scan for duplicates: ").strip()
            find_duplicate_files(Path(path))

        elif choice == "6":
            root = input("Root folder to organize OneDrive INTO (e.g. ~/Organized): ").strip()
            invoke_onedrive_organization(Path(root), dry_run=True)
            go = input("\nPreview looked good? Run for real? (Y/N): ").strip()
            if go.lower().startswith("y"):
                invoke_onedrive_organization(Path(root), dry_run=False)

        elif choice == "0":
            break
        else:
            print("Invalid choice.")


# ------------------------------------------------------------------------
# 10. CLI (for direct/scripted use, e.g. from Copilot-suggested commands)
# ------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="File Organizer for Mac")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create the folder structure")
    p_init.add_argument("--root", required=True)
    p_init.add_argument("--dry-run", action="store_true")

    p_plan = sub.add_parser("plan", help="Analyze + suggest sorting (CSV report only)")
    p_plan.add_argument("--source", required=True)
    p_plan.add_argument("--root", required=True)
    p_plan.add_argument("--report", default=None)

    p_org = sub.add_parser("organize", help="Move files into the folder structure")
    p_org.add_argument("--source", required=True)
    p_org.add_argument("--root", required=True)
    p_org.add_argument("--dry-run", action="store_true")

    p_ren = sub.add_parser("rename", help="Rename files to yyyy-MM-dd_Name.ext")
    p_ren.add_argument("--path", required=True)
    p_ren.add_argument("--recurse", action="store_true")
    p_ren.add_argument("--dry-run", action="store_true")

    p_dup = sub.add_parser("duplicates", help="Find (and optionally delete) duplicate files")
    p_dup.add_argument("--path", required=True)
    p_dup.add_argument("--delete-duplicates", action="store_true")
    p_dup.add_argument("--dry-run", action="store_true")

    p_od = sub.add_parser("onedrive", help="Organize your OneDrive folder")
    p_od.add_argument("--root", required=True)
    p_od.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command is None:
        show_menu()
        return

    if args.command == "init":
        initialize_folder_structure(Path(args.root), dry_run=args.dry_run)
    elif args.command == "plan":
        get_organization_plan(Path(args.source), Path(args.root),
                               Path(args.report) if args.report else None)
    elif args.command == "organize":
        invoke_file_organization(Path(args.source), Path(args.root), dry_run=args.dry_run)
    elif args.command == "rename":
        rename_files_to_standard(Path(args.path), dry_run=args.dry_run, recurse=args.recurse)
    elif args.command == "duplicates":
        find_duplicate_files(Path(args.path), delete_duplicates=args.delete_duplicates, dry_run=args.dry_run)
    elif args.command == "onedrive":
        invoke_onedrive_organization(Path(args.root), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
