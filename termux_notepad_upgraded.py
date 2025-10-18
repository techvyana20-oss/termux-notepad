#!/usr/bin/env python3
"""
Termux Notepad — Upgraded Terminal Editor
Features:
 - new/open/save/saveas
 - view with optional line numbers
 - append/insert/replace/delete lines
 - find (interactive) / replaceall
 - undo / redo (history stack)
 - autosave (interval) + backups folder
 - recent files list
 - stats, list, backup, settings
 - Ctrl+C safe handling
Designed for Termux / terminal environments (no GUI).
"""

import os
import sys
import shutil
import json
import time
import threading
import signal
from pathlib import Path
from datetime import datetime

# ---------------------
# Configuration / state
# ---------------------
APP_DIR = Path.home() / ".termux_notepad"
BACKUP_DIR = APP_DIR / "backups"
RECENT_FILE = APP_DIR / "recent.json"
SETTINGS_FILE = APP_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "autosave_interval_sec": 180,   # autosave every 3 minutes by default
    "max_undo": 60,
    "keep_backups": True,
    "backup_limit": 50
}

HOME = Path.cwd()

# Ensure app directories
APP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Load settings if present
def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                s = json.load(f)
            # ensure defaults for missing keys
            for k, v in DEFAULT_SETTINGS.items():
                if k not in s:
                    s[k] = v
            return s
        except Exception:
            return DEFAULT_SETTINGS.copy()
    else:
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()

def save_settings(s):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2)

SETTINGS = load_settings()

# ---------------------
# Utility functions
# ---------------------
def clear_screen():
    os.system('clear' if os.name == 'posix' else 'cls')

def prompt(msg='> '):
    try:
        return input(msg)
    except EOFError:
        return ''

def time_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_path(p):
    return Path(p).expanduser()

def pretty_path(p):
    try:
        return str(Path(p).resolve())
    except Exception:
        return str(p)

# Recent files handling
def load_recent():
    if RECENT_FILE.exists():
        try:
            with open(RECENT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_recent(lst):
    with open(RECENT_FILE, 'w', encoding='utf-8') as f:
        json.dump(lst[-100:], f, indent=2)

def add_recent(path):
    lst = load_recent()
    pstr = str(Path(path).resolve())
    if pstr in lst:
        lst.remove(pstr)
    lst.append(pstr)
    save_recent(lst)

# ---------------------
# Notepad class
# ---------------------
class Notepad:
    def __init__(self):
        self.buffer = []         # list of lines (no trailing newline)
        self.filepath = None     # Path or None
        self.modified = False
        self.line_numbers = True
        # undo/redo stacks store snapshots of buffer
        self.undo_stack = []
        self.redo_stack = []
        self.max_undo = SETTINGS.get("max_undo", 60)
        # autosave thread
        self.autosave_interval = SETTINGS.get("autosave_interval_sec", 180)
        self.autosave_enabled = True
        self._autosave_thread = None
        self._autosave_stop = threading.Event()
        self._start_autosave()

    # ---- Undo/Redo ----
    def _snapshot(self):
        """Store a snapshot for undo (deep copy)."""
        if len(self.undo_stack) >= self.max_undo:
            self.undo_stack.pop(0)
        self.undo_stack.append(list(self.buffer))
        # whenever we take a snapshot, clear redo
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            print("Nothing to undo.")
            return
        self.redo_stack.append(list(self.buffer))
        self.buffer = self.undo_stack.pop()
        self.modified = True
        print("Undo performed.")

    def redo(self):
        if not self.redo_stack:
            print("Nothing to redo.")
            return
        self.undo_stack.append(list(self.buffer))
        self.buffer = self.redo_stack.pop()
        self.modified = True
        print("Redo performed.")

    # ---- File operations ----
    def new(self):
        if self.modified and not self._confirm("Unsaved changes will be lost. Continue? (y/n): "):
            return
        self._snapshot()
        self.buffer = []
        self.filepath = None
        self.modified = False
        print("New buffer created.")

    def open(self, filename):
        path = safe_path(filename)
        if not path.exists():
            print("File not found:", path)
            return
        if self.modified and not self._confirm("Unsaved changes will be lost. Continue? (y/n): "):
            return
        # snapshot before replacing buffer (so open can be undone)
        self._snapshot()
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                self.buffer = [line.rstrip('\n') for line in f]
            self.filepath = path
            self.modified = False
            add_recent(path)
            print("Opened:", path)
        except Exception as e:
            print("Error opening file:", e)

    def save(self):
        if not self.filepath:
            return self.saveas(prompt("Save as (filename): ").strip())
        try:
            # create backup before overwriting if keep_backups true
            if SETTINGS.get("keep_backups", True):
                self._create_backup(self.filepath)
            with open(self.filepath, 'w', encoding='utf-8') as f:
                for line in self.buffer:
                    f.write(line + '\n')
            self.modified = False
            add_recent(self.filepath)
            print("Saved:", self.filepath)
        except Exception as e:
            print("Save failed:", e)

    def saveas(self, filename):
        if not filename:
            print("No filename provided.")
            return
        path = safe_path(filename)
        if path.exists():
            if not self._confirm(f"File {path} exists. Overwrite? (y/n): "):
                print("Save cancelled.")
                return
        try:
            if SETTINGS.get("keep_backups", True) and path.exists():
                self._create_backup(path)
            with open(path, 'w', encoding='utf-8') as f:
                for line in self.buffer:
                    f.write(line + '\n')
            self.filepath = path
            self.modified = False
            add_recent(path)
            print("Saved as:", path)
        except Exception as e:
            print("Save-as failed:", e)

    def delete_file(self, filename):
        path = safe_path(filename)
        if not path.exists():
            print("File not found:", path)
            return
        if self.filepath and self.filepath.samefile(path):
            if not self._confirm("You have this file open. Delete anyway? (y/n): "):
                return
        try:
            path.unlink()
            print("Deleted:", path)
        except Exception as e:
            print("Delete failed:", e)

    def rename_file(self, old, new):
        oldp = safe_path(old)
        newp = safe_path(new)
        if not oldp.exists():
            print("File not found:", oldp)
            return
        try:
            oldp.rename(newp)
            print(f"Renamed {oldp} -> {newp}")
            if self.filepath and self.filepath.samefile(oldp):
                self.filepath = newp
        except Exception as e:
            print("Rename failed:", e)

    def copy_file(self, src, dst):
        srcp = safe_path(src)
        dstp = safe_path(dst)
        if not srcp.exists():
            print("Source not found:", srcp)
            return
        try:
            shutil.copy2(srcp, dstp)
            print(f"Copied {srcp} -> {dstp}")
        except Exception as e:
            print("Copy failed:", e)

    # ---- Buffer editing ----
    def view(self, start=None, end=None):
        if not self.buffer:
            print("[Empty buffer]")
            return
        s = 1 if start is None else max(1, int(start))
        e = len(self.buffer) if end is None else min(len(self.buffer), int(end))
        for i in range(s-1, e):
            if self.line_numbers:
                print(f"{i+1:4d}: {self.buffer[i]}")
            else:
                print(self.buffer[i])
        print(f"--- showing lines {s} to {e} of {len(self.buffer)} ---")

    def append(self, text_lines=None):
        self._snapshot()
        print("Append mode. Type '.done' on a new line to finish.")
        if text_lines is None:
            while True:
                line = prompt()
                if line == '.done':
                    break
                self.buffer.append(line)
        else:
            self.buffer.extend(text_lines)
        self.modified = True
        print("Appended text.")

    def insert(self, line_no, text):
        self._snapshot()
        idx = max(0, int(line_no)-1)
        if idx > len(self.buffer):
            # pad empty lines
            self.buffer.extend([''] * (idx - len(self.buffer)))
        self.buffer.insert(idx, text)
        self.modified = True
        print(f"Inserted at line {idx+1}.")

    def replace_line(self, line_no, text):
        idx = int(line_no)-1
        if 0 <= idx < len(self.buffer):
            self._snapshot()
            self.buffer[idx] = text
            self.modified = True
            print(f"Replaced line {idx+1}.")
        else:
            print("Line number out of range.")

    def delete_line(self, line_no):
        idx = int(line_no)-1
        if 0 <= idx < len(self.buffer):
            self._snapshot()
            removed = self.buffer.pop(idx)
            self.modified = True
            print(f"Deleted line {idx+1}: {removed}")
        else:
            print("Line number out of range.")

    def find_interactive(self, needle):
        """Find occurrences and optionally replace each with confirmation."""
        if not needle:
            print("Empty search.")
            return
        found = False
        for i, line in enumerate(self.buffer, 1):
            if needle in line:
                found = True
                print(f"Match at {i}: {line}")
                ans = prompt("Replace this occurrence? (y = replace, n = skip, a = replace all, q = quit) ").strip().lower()
                if ans == 'y':
                    repl = prompt("Replacement text: ")
                    self._snapshot()
                    self.buffer[i-1] = line.replace(needle, repl)
                    self.modified = True
                elif ans == 'a':
                    repl = prompt("Replacement text (replace ALL remaining): ")
                    self.replace_all(needle, repl)
                    return
                elif ans == 'q':
                    return
                # n => skip
        if not found:
            print("No matches found.")

    def replace_all(self, old, new):
        if not old:
            print("Old text empty.")
            return
        count = 0
        self._snapshot()
        for i, line in enumerate(self.buffer):
            if old in line:
                self.buffer[i] = line.replace(old, new)
                count += 1
        if count:
            self.modified = True
        print(f"Replaced occurrences in {count} lines.")

    def stats(self):
        lines = len(self.buffer)
        words = sum(len(line.split()) for line in self.buffer)
        chars = sum(len(line) for line in self.buffer)
        print(f"Lines: {lines}, Words: {words}, Characters: {chars}")

    # ---- Backups ----
    def _create_backup(self, path=None):
        if path is None and self.filepath is None:
            # backup of buffer to timestamped file
            name = f"buffer_{time_stamp()}.bak"
            target = BACKUP_DIR / name
        elif path is not None:
            p = Path(path)
            name = f"{p.name}.{time_stamp()}.bak"
            target = BACKUP_DIR / name
        else:
            p = Path(self.filepath)
            name = f"{p.name}.{time_stamp()}.bak"
            target = BACKUP_DIR / name
        try:
            with open(target, 'w', encoding='utf-8') as f:
                for line in self.buffer:
                    f.write(line + '\n')
        except Exception as e:
            print("Backup failed:", e)
            return
        # cleanup old backups if limit set
        limit = SETTINGS.get("backup_limit", 50)
        if limit > 0:
            all_backups = sorted(BACKUP_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for b in all_backups[limit:]:
                try:
                    b.unlink()
                except Exception:
                    pass
        print("Backup created:", target)

    def manual_backup(self):
        self._create_backup(self.filepath)

    # ---- Autosave ----
    def _autosave_worker(self):
        while not self._autosave_stop.wait(self.autosave_interval):
            if not self.autosave_enabled:
                continue
            try:
                if self.modified:
                    tmp_name = APP_DIR / f"autosave_{time_stamp()}.tmp"
                    with open(tmp_name, 'w', encoding='utf-8') as f:
                        for line in self.buffer:
                            f.write(line + '\n')
                    # keep small number of autosaves
                    # move to backups for persistence
                    if SETTINGS.get("keep_backups", True):
                        backup_name = BACKUP_DIR / f"autosave_{time_stamp()}.bak"
                        shutil.copy2(tmp_name, backup_name)
                    # remove tmp
                    try:
                        tmp_name.unlink()
                    except Exception:
                        pass
                    print(f"[autosave] buffer saved at {datetime.now().strftime('%H:%M:%S')}")
            except Exception as e:
                print("Autosave error:", e)

    def _start_autosave(self):
        if self._autosave_thread and self._autosave_thread.is_alive():
            return
        self._autosave_stop.clear()
        self._autosave_thread = threading.Thread(target=self._autosave_worker, daemon=True)
        self._autosave_thread.start()

    def stop_autosave(self):
        self._autosave_stop.set()
        if self._autosave_thread:
            self._autosave_thread.join(timeout=0.1)

    # ---- Settings ----
    def set_setting(self, key, value):
        SETTINGS[key] = value
        save_settings(SETTINGS)
        # reflect runtime changes
        if key == "autosave_interval_sec":
            try:
                self.autosave_interval = int(value)
            except Exception:
                pass
        if key == "max_undo":
            try:
                self.max_undo = int(value)
            except Exception:
                pass
        print("Setting updated:", key)

    # ---- Small helpers ----
    def _confirm(self, msg):
        ans = prompt(msg).strip().lower()
        return ans in ('y', 'yes')

    # ---- Exit handler ----
    def exit(self):
        # autosave on exit if modified
        if self.modified:
            if self._confirm("You have unsaved changes. Save before exit? (y/n): "):
                self.save()
            else:
                # create a backup so changes aren't lost completely
                self._create_backup()
        self.stop_autosave()
        print("Goodbye!")

# ---------------------
# Helper UI / commands
# ---------------------
def print_help():
    h = """
Commands:
 new                    - create new buffer
 open <file>            - open existing file
 save                   - save current buffer (asks name if new)
 saveas <file>          - save as specified filename
 view [s] [e]           - view lines from s to e
 linenumbers            - toggle line numbers on/off for view
 append                 - append lines (type .done when finished)
 insert <n>             - insert a line before line n
 replace <n>            - replace line n
 delete <n>             - delete line n
 find <text>            - find occurrences (interactive replace options)
 replaceall <old> <new> - replace all old with new
 undo                   - undo last change
 redo                   - redo
 stats                  - show lines/words/chars
 list                   - list files in current directory
 recent                 - show recently opened files
 openrecent <n>         - open recent file by number from 'recent'
 backup                 - create a manual backup of current buffer
 deletefile <file>      - delete a file
 rename <old> <new>     - rename file
 copy <src> <dst>       - copy file
 settings               - show current settings
 set <key> <value>      - change a setting (autosave_interval_sec, max_undo, keep_backups, backup_limit)
 help                   - show this help
 clear                  - clear terminal
 quit                   - quit editor
"""
    print(h)

def list_text_files(folder=HOME):
    files = [f for f in Path(folder).iterdir() if f.is_file()]
    files_sorted = sorted(files, key=lambda x: x.name.lower())
    for i, f in enumerate(files_sorted, 1):
        print(f"{i}. {f.name}")
    if not files_sorted:
        print("No files found.")

# Ctrl+C handling
def install_signal_handler(note):
    def handler(sig, frame):
        print("\n[Interrupt]")
        try:
            if note.modified:
                if note._confirm("You have unsaved changes. Save now? (y/n): "):
                    note.save()
        except Exception:
            pass
        # do not exit immediately; return to prompt
    signal.signal(signal.SIGINT, handler)

# ---------------------
# Main loop
# ---------------------
def main():
    clear_screen()
    note = Notepad()
    install_signal_handler(note)
    print("Termux Notepad — upgraded (type 'help' for commands)\n")
    print("Tip: use 'settings' to view autosave interval, max_undo, and backup options.\n")
    while True:
        try:
            name = note.filepath.name if note.filepath else "untitled"
            star = '*' if note.modified else ''
            cmdline = prompt(f"[{name}]{star}> ").strip()
        except EOFError:
            cmdline = 'quit'

        if not cmdline:
            continue
        parts = cmdline.split()
        cmd = parts[0].lower()

        try:
            if cmd == 'help':
                print_help()

            elif cmd == 'new':
                note.new()

            elif cmd == 'open':
                if len(parts) < 2:
                    print("Usage: open <filename>")
                else:
                    note.open(parts[1])

            elif cmd == 'save':
                note.save()

            elif cmd == 'saveas':
                if len(parts) < 2:
                    print("Usage: saveas <filename>")
                else:
                    note.saveas(parts[1])

            elif cmd == 'view':
                if len(parts) == 1:
                    note.view()
                elif len(parts) == 2:
                    note.view(parts[1])
                else:
                    note.view(parts[1], parts[2])

            elif cmd == 'linenumbers':
                note.line_numbers = not note.line_numbers
                print("Line numbers", "ON" if note.line_numbers else "OFF")

            elif cmd == 'append':
                note.append()

            elif cmd == 'insert':
                if len(parts) < 2:
                    print("Usage: insert <line_no>")
                else:
                    line_no = parts[1]
                    text = prompt("Text to insert: ")
                    note.insert(line_no, text)

            elif cmd == 'replace':
                if len(parts) < 2:
                    print("Usage: replace <line_no>")
                else:
                    ln = parts[1]
                    text = prompt("Replacement text: ")
                    note.replace_line(ln, text)

            elif cmd == 'delete':
                if len(parts) < 2:
                    print("Usage: delete <line_no>")
                else:
                    note.delete_line(parts[1])

            elif cmd == 'find':
                if len(parts) < 2:
                    print("Usage: find <text>")
                else:
                    needle = cmdline.partition(' ')[2]
                    note.find_interactive(needle)

            elif cmd == 'replaceall':
                if len(parts) < 3:
                    print("Usage: replaceall <old> <new>")
                else:
                    old = parts[1]
                    new = parts[2]
                    note.replace_all(old, new)

            elif cmd == 'undo':
                note.undo()

            elif cmd == 'redo':
                note.redo()

            elif cmd == 'stats':
                note.stats()

            elif cmd == 'list':
                list_text_files(HOME)

            elif cmd == 'recent':
                lst = load_recent()
                if not lst:
                    print("No recent files.")
                else:
                    for i, p in enumerate(reversed(lst), 1):
                        print(f"{i}. {p}")

            elif cmd == 'openrecent':
                if len(parts) < 2:
                    print("Usage: openrecent <n>")
                else:
                    idx = int(parts[1])
                    lst = load_recent()
                    if not lst:
                        print("No recent files.")
                    else:
                        # reversed display earlier; map index
                        rev = list(reversed(lst))
                        if 1 <= idx <= len(rev):
                            note.open(rev[idx-1])
                        else:
                            print("Index out of range.")

            elif cmd == 'backup':
                note.manual_backup()

            elif cmd == 'deletefile':
                if len(parts) < 2:
                    print("Usage: deletefile <filename>")
                else:
                    note.delete_file(parts[1])

            elif cmd == 'rename':
                if len(parts) < 3:
                    print("Usage: rename <old> <new>")
                else:
                    note.rename_file(parts[1], parts[2])

            elif cmd == 'copy':
                if len(parts) < 3:
                    print("Usage: copy <src> <dst>")
                else:
                    note.copy_file(parts[1], parts[2])

            elif cmd == 'settings':
                print(json.dumps(SETTINGS, indent=2))

            elif cmd == 'set':
                if len(parts) < 3:
                    print("Usage: set <key> <value>")
                else:
                    key = parts[1]
                    val = parts[2]
                    # interpret booleans and ints
                    if val.lower() in ('true', 'false'):
                        vv = val.lower() == 'true'
                    else:
                        try:
                            vv = int(val)
                        except Exception:
                            vv = val
                    note.set_setting(key, vv)

            elif cmd == 'clear':
                clear_screen()

            elif cmd == 'quit':
                note.exit()
                break

            else:
                print("Unknown command. Type 'help' for commands.")

        except Exception as e:
            print("Error:", e)

if __name__ == '__main__':
    main()
