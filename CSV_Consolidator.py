#!/usr/bin/env python3
"""
CSV Power Tool
Professional-grade CSV file combiner and processor.
Merge, filter, transform, deduplicate, and export CSV data with full control.
"""

import subprocess
import sys

def install_dependencies():
    """Install required packages if not present."""
    required = ["customtkinter", "tkinterdnd2"]
    for package in required:
        try:
            __import__(package)
        except ImportError:
            print(f"Installing {package}...")
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", package,
                "--quiet", "--break-system-packages"
            ])

install_dependencies()

import csv
import re
import json
import threading
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable
from tkinter import filedialog, StringVar, BooleanVar, IntVar, END
import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# THEME CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

COLORS = {
    "bg_dark": "#020617",
    "bg_secondary": "#0f172a",
    "bg_tertiary": "#1e293b",
    "bg_input": "#0f172a",
    "bg_hover": "#334155",
    "border": "#334155",
    "border_light": "#475569",
    "text_primary": "#f8fafc",
    "text_secondary": "#94a3b8",
    "text_muted": "#64748b",
    "accent_green": "#22c55e",
    "accent_green_hover": "#16a34a",
    "accent_blue": "#60a5fa",
    "accent_blue_hover": "#3b82f6",
    "accent_purple": "#a78bfa",
    "accent_purple_hover": "#8b5cf6",
    "accent_orange": "#f97316",
    "accent_red": "#ef4444",
    "accent_cyan": "#22d3ee",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "tab_active": "#1e293b",
    "tab_inactive": "#0f172a",
}

FONTS = {
    "title": ("Segoe UI", 24, "bold"),
    "heading": ("Segoe UI", 14, "bold"),
    "subheading": ("Segoe UI", 12, "bold"),
    "body": ("Segoe UI", 12),
    "small": ("Segoe UI", 11),
    "mono": ("Consolas", 11),
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProcessingConfig:
    """Configuration for CSV processing."""
    # Column selection
    columns_mode: str = "all"  # "all", "select", "exclude"
    selected_columns: list = field(default_factory=list)
    column_mapping: dict = field(default_factory=dict)  # {original: renamed}
    column_order: list = field(default_factory=list)
    
    # Sorting
    sort_enabled: bool = False
    sort_columns: list = field(default_factory=list)  # [(column, ascending), ...]
    sort_case_sensitive: bool = False
    sort_numeric_aware: bool = True
    
    # Deduplication
    dedupe_enabled: bool = True
    dedupe_columns: list = field(default_factory=list)  # Empty = all columns
    dedupe_keep: str = "first"  # "first", "last", "none"
    
    # Filtering
    filters: list = field(default_factory=list)  # [(column, operator, value), ...]
    filter_logic: str = "and"  # "and", "or"
    
    # Transformations
    trim_whitespace: bool = True
    case_transform: str = "none"  # "none", "upper", "lower", "title"
    empty_value: str = ""  # Replace empty cells with this
    
    # Output
    output_delimiter: str = ","
    output_encoding: str = "utf-8"
    output_quoting: str = "minimal"  # "minimal", "all", "nonnumeric", "none"
    include_header: bool = True
    line_ending: str = "auto"  # "auto", "unix", "windows"


@dataclass 
class ProcessingStats:
    """Statistics from processing."""
    files_processed: int = 0
    files_skipped: int = 0
    total_rows_read: int = 0
    rows_filtered: int = 0
    duplicates_removed: int = 0
    final_row_count: int = 0
    unique_columns: int = 0
    errors: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# CSV PROCESSING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class CSVEngine:
    """Core CSV processing engine."""
    
    FILTER_OPERATORS = {
        "equals": lambda v, t: str(v).lower() == str(t).lower(),
        "not_equals": lambda v, t: str(v).lower() != str(t).lower(),
        "contains": lambda v, t: str(t).lower() in str(v).lower(),
        "not_contains": lambda v, t: str(t).lower() not in str(v).lower(),
        "starts_with": lambda v, t: str(v).lower().startswith(str(t).lower()),
        "ends_with": lambda v, t: str(v).lower().endswith(str(t).lower()),
        "is_empty": lambda v, t: not str(v).strip(),
        "is_not_empty": lambda v, t: bool(str(v).strip()),
        "greater_than": lambda v, t: CSVEngine._numeric_compare(v, t, ">"),
        "less_than": lambda v, t: CSVEngine._numeric_compare(v, t, "<"),
        "regex": lambda v, t: bool(re.search(t, str(v), re.IGNORECASE)),
    }
    
    @staticmethod
    def _numeric_compare(v, t, op):
        try:
            v_num = float(str(v).replace(",", ""))
            t_num = float(str(t).replace(",", ""))
            if op == ">":
                return v_num > t_num
            return v_num < t_num
        except ValueError:
            return False
    
    def __init__(self, config: ProcessingConfig, 
                 progress_callback: Callable = None,
                 log_callback: Callable = None):
        self.config = config
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.cancelled = False
        self.stats = ProcessingStats()
    
    def log(self, message: str, level: str = "info"):
        if self.log_callback:
            self.log_callback(message, level)
    
    def update_progress(self, value: float, status: str):
        if self.progress_callback:
            self.progress_callback(value, status)
    
    def cancel(self):
        self.cancelled = True
    
    def discover_columns(self, files: list[Path]) -> list[str]:
        """Discover all unique columns across files."""
        all_columns = []
        seen = set()
        
        for file_path in files:
            try:
                with open(file_path, 'r', encoding='utf-8', newline='') as f:
                    reader = csv.reader(f)
                    headers = next(reader, [])
                    for col in headers:
                        col = col.strip()
                        if col and col not in seen:
                            all_columns.append(col)
                            seen.add(col)
            except:
                try:
                    with open(file_path, 'r', encoding='latin-1', newline='') as f:
                        reader = csv.reader(f)
                        headers = next(reader, [])
                        for col in headers:
                            col = col.strip()
                            if col and col not in seen:
                                all_columns.append(col)
                                seen.add(col)
                except:
                    pass
        
        return all_columns
    
    def process(self, input_files: list[Path], output_file: Path) -> ProcessingStats:
        """Process CSV files according to configuration."""
        self.cancelled = False
        self.stats = ProcessingStats()
        
        all_rows = []
        all_columns = set()
        column_order = []
        
        total_files = len(input_files)
        
        # Phase 1: Read all files
        self.log("Phase 1: Reading files...", "info")
        
        for idx, csv_path in enumerate(input_files):
            if self.cancelled:
                self.log("Processing cancelled", "warning")
                return self.stats
            
            progress = (idx / total_files) * 40
            self.update_progress(progress, f"Reading {csv_path.name}...")
            
            rows_from_file = self._read_file(csv_path, all_columns, column_order)
            all_rows.extend(rows_from_file)
        
        if not all_rows:
            self.log("No data to process", "warning")
            return self.stats
        
        self.stats.unique_columns = len(all_columns)
        
        # Determine final columns
        final_columns = self._get_final_columns(column_order)
        
        # Phase 2: Apply filters
        if self.config.filters and not self.cancelled:
            self.update_progress(45, "Applying filters...")
            self.log(f"Phase 2: Applying {len(self.config.filters)} filter(s)...", "info")
            all_rows = self._apply_filters(all_rows)
        
        # Phase 3: Apply transformations
        if not self.cancelled:
            self.update_progress(55, "Applying transformations...")
            self.log("Phase 3: Applying transformations...", "info")
            all_rows = self._apply_transformations(all_rows, final_columns)
        
        # Phase 4: Deduplicate
        if self.config.dedupe_enabled and not self.cancelled:
            self.update_progress(65, "Removing duplicates...")
            self.log("Phase 4: Removing duplicates...", "info")
            all_rows = self._deduplicate(all_rows, final_columns)
        
        # Phase 5: Sort
        if self.config.sort_enabled and self.config.sort_columns and not self.cancelled:
            self.update_progress(80, "Sorting data...")
            self.log("Phase 5: Sorting data...", "info")
            all_rows = self._sort_rows(all_rows, final_columns)
        
        # Phase 6: Write output
        if not self.cancelled:
            self.update_progress(90, "Writing output file...")
            self.log("Phase 6: Writing output...", "info")
            self._write_output(all_rows, final_columns, output_file)
        
        self.stats.final_row_count = len(all_rows)
        self.update_progress(100, "Complete!")
        
        return self.stats
    
    def _read_file(self, file_path: Path, all_columns: set, column_order: list) -> list[dict]:
        """Read a single CSV file."""
        rows = []
        
        if not file_path.exists():
            self.log(f"✗ File not found: {file_path.name}", "error")
            self.stats.files_skipped += 1
            self.stats.errors.append(f"Not found: {file_path.name}")
            return rows
        
        encodings = ['utf-8', 'latin-1', 'cp1252', 'utf-16']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, newline='') as f:
                    # Detect delimiter
                    sample = f.read(4096)
                    f.seek(0)
                    
                    delimiter = ','
                    for delim in [',', '\t', ';', '|']:
                        if sample.count(delim) > sample.count(delimiter):
                            delimiter = delim
                    
                    reader = csv.DictReader(f, delimiter=delimiter)
                    
                    if reader.fieldnames:
                        for col in reader.fieldnames:
                            col = col.strip() if col else ""
                            if col and col not in all_columns:
                                all_columns.add(col)
                                column_order.append(col)
                    
                    row_count = 0
                    for row in reader:
                        cleaned_row = {}
                        for k, v in row.items():
                            key = k.strip() if k else ""
                            if key:
                                cleaned_row[key] = v.strip() if v and self.config.trim_whitespace else (v or "")
                        rows.append(cleaned_row)
                        row_count += 1
                    
                    self.stats.files_processed += 1
                    self.stats.total_rows_read += row_count
                    self.log(f"✓ {file_path.name} ({row_count:,} rows)", "success")
                    return rows
                    
            except UnicodeDecodeError:
                continue
            except Exception as e:
                if encoding == encodings[-1]:
                    self.log(f"✗ Error reading {file_path.name}: {e}", "error")
                    self.stats.files_skipped += 1
                    self.stats.errors.append(f"{file_path.name}: {e}")
                continue
        
        return rows
    
    def _get_final_columns(self, discovered_order: list) -> list[str]:
        """Determine final column list based on configuration."""
        if self.config.column_order:
            return self.config.column_order
        
        if self.config.columns_mode == "all":
            columns = discovered_order
        elif self.config.columns_mode == "select":
            columns = [c for c in discovered_order if c in self.config.selected_columns]
        elif self.config.columns_mode == "exclude":
            columns = [c for c in discovered_order if c not in self.config.selected_columns]
        else:
            columns = discovered_order
        
        # Apply column mapping for display names
        return columns
    
    def _apply_filters(self, rows: list[dict]) -> list[dict]:
        """Apply configured filters to rows."""
        if not self.config.filters:
            return rows
        
        filtered = []
        original_count = len(rows)
        
        for row in rows:
            results = []
            for col, operator, value in self.config.filters:
                cell_value = row.get(col, "")
                op_func = self.FILTER_OPERATORS.get(operator)
                if op_func:
                    results.append(op_func(cell_value, value))
                else:
                    results.append(True)
            
            if self.config.filter_logic == "and":
                keep = all(results) if results else True
            else:
                keep = any(results) if results else True
            
            if keep:
                filtered.append(row)
        
        self.stats.rows_filtered = original_count - len(filtered)
        self.log(f"  Filtered out {self.stats.rows_filtered:,} rows", "info")
        
        return filtered
    
    def _apply_transformations(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Apply configured transformations to data."""
        transformed = []
        
        for row in rows:
            new_row = {}
            for col in columns:
                value = row.get(col, self.config.empty_value)
                
                if value is None:
                    value = self.config.empty_value
                
                # Case transformation
                if self.config.case_transform == "upper":
                    value = str(value).upper()
                elif self.config.case_transform == "lower":
                    value = str(value).lower()
                elif self.config.case_transform == "title":
                    value = str(value).title()
                
                # Apply column mapping
                output_col = self.config.column_mapping.get(col, col)
                new_row[output_col] = value
            
            transformed.append(new_row)
        
        return transformed
    
    def _deduplicate(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Remove duplicate rows."""
        if not rows:
            return rows
        
        dedupe_cols = self.config.dedupe_columns if self.config.dedupe_columns else columns
        
        seen = {}
        result = []
        
        for idx, row in enumerate(rows):
            # Create key from dedupe columns
            key_parts = []
            for col in dedupe_cols:
                mapped_col = self.config.column_mapping.get(col, col)
                val = row.get(mapped_col, row.get(col, ""))
                key_parts.append(str(val).lower() if not self.config.sort_case_sensitive else str(val))
            key = tuple(key_parts)
            
            if key not in seen:
                seen[key] = idx
                result.append(row)
            elif self.config.dedupe_keep == "last":
                # Replace earlier occurrence
                old_idx = seen[key]
                for i, r in enumerate(result):
                    if i == old_idx:
                        result[i] = row
                        break
        
        self.stats.duplicates_removed = len(rows) - len(result)
        self.log(f"  Removed {self.stats.duplicates_removed:,} duplicates", "info")
        
        return result
    
    def _sort_rows(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Sort rows by configured columns."""
        if not rows or not self.config.sort_columns:
            return rows
        
        def sort_key(row):
            keys = []
            for col, ascending in self.config.sort_columns:
                mapped_col = self.config.column_mapping.get(col, col)
                val = row.get(mapped_col, row.get(col, ""))
                
                if self.config.sort_numeric_aware:
                    try:
                        val = float(str(val).replace(",", ""))
                    except ValueError:
                        if not self.config.sort_case_sensitive:
                            val = str(val).lower()
                else:
                    if not self.config.sort_case_sensitive:
                        val = str(val).lower()
                
                keys.append(val)
            return keys
        
        # Determine sort direction for each column
        reverse_flags = [not asc for _, asc in self.config.sort_columns]
        
        # Multi-column sort requires custom approach
        # Sort by last column first, then work backwards
        sorted_rows = rows.copy()
        for i in range(len(self.config.sort_columns) - 1, -1, -1):
            col, ascending = self.config.sort_columns[i]
            mapped_col = self.config.column_mapping.get(col, col)
            
            def make_key(row, mc=mapped_col, c=col, na=self.config.sort_numeric_aware, cs=self.config.sort_case_sensitive):
                val = row.get(mc, row.get(c, ""))
                if na:
                    try:
                        return (0, float(str(val).replace(",", "")))
                    except ValueError:
                        return (1, str(val).lower() if not cs else str(val))
                return str(val).lower() if not cs else str(val)
            
            sorted_rows.sort(key=make_key, reverse=not ascending)
        
        self.log(f"  Sorted by {len(self.config.sort_columns)} column(s)", "info")
        
        return sorted_rows
    
    def _write_output(self, rows: list[dict], columns: list[str], output_file: Path):
        """Write processed data to output file."""
        if not rows:
            self.log("No data to write", "warning")
            return
        
        # Map columns to output names
        output_columns = [self.config.column_mapping.get(c, c) for c in columns]
        
        quoting_map = {
            "minimal": csv.QUOTE_MINIMAL,
            "all": csv.QUOTE_ALL,
            "nonnumeric": csv.QUOTE_NONNUMERIC,
            "none": csv.QUOTE_NONE,
        }
        quoting = quoting_map.get(self.config.output_quoting, csv.QUOTE_MINIMAL)
        
        # Line ending
        if self.config.line_ending == "unix":
            newline = "\n"
        elif self.config.line_ending == "windows":
            newline = "\r\n"
        else:
            newline = ""
        
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', encoding=self.config.output_encoding, 
                      newline=newline if newline else '') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=output_columns,
                    delimiter=self.config.output_delimiter,
                    quoting=quoting,
                    extrasaction='ignore'
                )
                
                if self.config.include_header:
                    writer.writeheader()
                
                for row in rows:
                    writer.writerow(row)
            
            self.log(f"✓ Saved: {output_file.name} ({len(rows):,} rows)", "success")
            
        except Exception as e:
            self.log(f"✗ Error writing file: {e}", "error")
            self.stats.errors.append(f"Write error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# GUI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════

class FileListPanel(ctk.CTkFrame):
    """File list with drag-drop support."""
    
    def __init__(self, master, on_change: Callable = None, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.files: list[Path] = []
        self.on_change = on_change
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📁 Input Files",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        self.count_label = ctk.CTkLabel(
            header, text="0 files",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.count_label.pack(side="right")
        
        # List container
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.scroll_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent_blue"]
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.scroll_frame,
            text="Drag & drop CSV files here\nor use buttons below",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(expand=True, pady=30)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="Add Files", font=ctk.CTkFont(size=12),
            height=32, fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            corner_radius=6, command=self._browse_files
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        
        ctk.CTkButton(
            btn_frame, text="Add Folder", font=ctk.CTkFont(size=12),
            height=32, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=6, command=self._browse_folder
        ).pack(side="left", fill="x", expand=True, padx=(4, 4))
        
        ctk.CTkButton(
            btn_frame, text="Clear", font=ctk.CTkFont(size=12),
            height=32, width=60, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self.clear
        ).pack(side="left", padx=(4, 0))
    
    def _browse_files(self):
        files = filedialog.askopenfilenames(
            title="Select CSV Files",
            filetypes=[("CSV Files", "*.csv"), ("TSV Files", "*.tsv"), 
                       ("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if files:
            self.add_files([Path(f) for f in files])
    
    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            folder_path = Path(folder)
            csv_files = list(folder_path.glob("*.csv")) + list(folder_path.glob("*.tsv"))
            if csv_files:
                self.add_files(csv_files)
    
    def add_files(self, paths: list[Path]) -> int:
        added = 0
        for p in paths:
            path = Path(p) if not isinstance(p, Path) else p
            if path.suffix.lower() in ['.csv', '.tsv', '.txt'] and path not in self.files:
                self.files.append(path)
                added += 1
        
        if added:
            self._refresh()
            if self.on_change:
                self.on_change()
        return added
    
    def remove_file(self, path: Path):
        if path in self.files:
            self.files.remove(path)
            self._refresh()
            if self.on_change:
                self.on_change()
    
    def clear(self):
        self.files.clear()
        self._refresh()
        if self.on_change:
            self.on_change()
    
    def _refresh(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        
        if not self.files:
            self.placeholder = ctk.CTkLabel(
                self.scroll_frame,
                text="Drag & drop CSV files here\nor use buttons below",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(expand=True, pady=30)
            self.count_label.configure(text="0 files")
        else:
            for idx, fp in enumerate(self.files):
                self._create_item(fp, idx)
            self.count_label.configure(text=f"{len(self.files)} file{'s' if len(self.files) != 1 else ''}")
    
    def _create_item(self, path: Path, idx: int):
        bg = COLORS["bg_secondary"] if idx % 2 == 0 else COLORS["bg_tertiary"]
        
        frame = ctk.CTkFrame(self.scroll_frame, fg_color=bg, corner_radius=4, height=32)
        frame.pack(fill="x", pady=1)
        frame.pack_propagate(False)
        
        ctk.CTkLabel(frame, text="📄", font=ctk.CTkFont(size=12), width=24).pack(side="left", padx=(6, 2))
        
        ctk.CTkLabel(
            frame, text=path.name, font=ctk.CTkFont(size=11),
            text_color=COLORS["text_primary"], anchor="w"
        ).pack(side="left", fill="x", expand=True)
        
        try:
            size = path.stat().st_size
            size_text = f"{size/1024:.1f}KB" if size >= 1024 else f"{size}B"
        except:
            size_text = ""
        
        ctk.CTkLabel(
            frame, text=size_text, font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"], width=50
        ).pack(side="right", padx=4)
        
        ctk.CTkButton(
            frame, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda p=path: self.remove_file(p)
        ).pack(side="right", padx=2)


class ColumnPanel(ctk.CTkFrame):
    """Column selection and configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.selected: set[str] = set()
        self.column_mapping: dict[str, str] = {}
        self.mode = StringVar(value="all")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📊 Column Selection",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        self.count_label = ctk.CTkLabel(
            header, text="No columns",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.count_label.pack(side="right")
        
        # Mode selection
        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        for mode, text in [("all", "All Columns"), ("select", "Include Selected"), ("exclude", "Exclude Selected")]:
            ctk.CTkRadioButton(
                mode_frame, text=text, variable=self.mode, value=mode,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["accent_blue_hover"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 12))
        
        # Column list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.scroll_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent_blue"]
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.scroll_frame,
            text="Add files to discover columns",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(expand=True, pady=20)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="Select All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._select_all
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            btn_frame, text="Select None", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._select_none
        ).pack(side="left")
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
        self.selected = set(columns)
        self._refresh()
    
    def _select_all(self):
        self.selected = set(self.columns)
        self._refresh()
    
    def _select_none(self):
        self.selected.clear()
        self._refresh()
    
    def _toggle_column(self, col: str, var: BooleanVar):
        if var.get():
            self.selected.add(col)
        else:
            self.selected.discard(col)
    
    def _refresh(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        
        if not self.columns:
            self.placeholder = ctk.CTkLabel(
                self.scroll_frame,
                text="Add files to discover columns",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(expand=True, pady=20)
            self.count_label.configure(text="No columns")
        else:
            for col in self.columns:
                var = BooleanVar(value=col in self.selected)
                
                frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent", height=28)
                frame.pack(fill="x", pady=1)
                
                ctk.CTkCheckBox(
                    frame, text=col, variable=var,
                    font=ctk.CTkFont(size=11),
                    fg_color=COLORS["accent_blue"],
                    hover_color=COLORS["accent_blue_hover"],
                    text_color=COLORS["text_primary"],
                    command=lambda c=col, v=var: self._toggle_column(c, v)
                ).pack(side="left")
            
            self.count_label.configure(text=f"{len(self.columns)} columns")
    
    def get_config(self) -> tuple[str, list[str]]:
        return self.mode.get(), list(self.selected)


class SortPanel(ctk.CTkFrame):
    """Sorting configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.sort_rules: list[tuple[str, bool]] = []  # (column, ascending)
        
        self.enabled = BooleanVar(value=False)
        self.case_sensitive = BooleanVar(value=False)
        self.numeric_aware = BooleanVar(value=True)
        
        # Header with enable toggle
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkSwitch(
            header, text="🔤 Sorting",
            variable=self.enabled,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"],
            button_color=COLORS["text_secondary"],
            button_hover_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Options
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkCheckBox(
            opts_frame, text="Case sensitive", variable=self.case_sensitive,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 12))
        
        ctk.CTkCheckBox(
            opts_frame, text="Numeric-aware", variable=self.numeric_aware,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left")
        
        # Sort rules list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.rules_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.rules_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        # Add rule button
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="+ Add Sort Rule", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["accent_purple"],
            hover_color=COLORS["accent_purple_hover"],
            corner_radius=4, command=self._add_rule
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame, text="Clear All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._clear_rules
        ).pack(side="right")
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
    
    def _add_rule(self):
        if not self.columns:
            return
        
        self.sort_rules.append((self.columns[0], True))
        self._refresh_rules()
    
    def _remove_rule(self, idx: int):
        if 0 <= idx < len(self.sort_rules):
            self.sort_rules.pop(idx)
            self._refresh_rules()
    
    def _clear_rules(self):
        self.sort_rules.clear()
        self._refresh_rules()
    
    def _update_rule(self, idx: int, column: str = None, ascending: bool = None):
        if 0 <= idx < len(self.sort_rules):
            col, asc = self.sort_rules[idx]
            self.sort_rules[idx] = (column if column is not None else col,
                                     ascending if ascending is not None else asc)
    
    def _refresh_rules(self):
        for w in self.rules_frame.winfo_children():
            w.destroy()
        
        if not self.sort_rules:
            ctk.CTkLabel(
                self.rules_frame,
                text="No sort rules defined",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            ).pack(pady=10)
        else:
            for idx, (col, asc) in enumerate(self.sort_rules):
                self._create_rule_row(idx, col, asc)
    
    def _create_rule_row(self, idx: int, column: str, ascending: bool):
        frame = ctk.CTkFrame(self.rules_frame, fg_color=COLORS["bg_secondary"], corner_radius=4, height=36)
        frame.pack(fill="x", pady=2)
        frame.pack_propagate(False)
        
        ctk.CTkLabel(
            frame, text=f"{idx + 1}.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"], width=24
        ).pack(side="left", padx=(8, 4))
        
        col_var = StringVar(value=column)
        col_menu = ctk.CTkOptionMenu(
            frame, variable=col_var, values=self.columns,
            font=ctk.CTkFont(size=11), height=28, width=150,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            button_hover_color=COLORS["bg_hover"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_rule(i, column=v)
        )
        col_menu.pack(side="left", padx=4)
        
        dir_var = StringVar(value="A→Z" if ascending else "Z→A")
        dir_menu = ctk.CTkOptionMenu(
            frame, variable=dir_var, values=["A→Z", "Z→A"],
            font=ctk.CTkFont(size=11), height=28, width=70,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            button_hover_color=COLORS["bg_hover"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_rule(i, ascending=(v == "A→Z"))
        )
        dir_menu.pack(side="left", padx=4)
        
        ctk.CTkButton(
            frame, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda i=idx: self._remove_rule(i)
        ).pack(side="right", padx=4)
    
    def get_config(self) -> tuple[bool, list, bool, bool]:
        return self.enabled.get(), self.sort_rules.copy(), self.case_sensitive.get(), self.numeric_aware.get()


class DedupePanel(ctk.CTkFrame):
    """Deduplication configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.selected_columns: set[str] = set()
        
        self.enabled = BooleanVar(value=True)
        self.keep_mode = StringVar(value="first")
        self.use_all_columns = BooleanVar(value=True)
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkSwitch(
            header, text="🔄 Deduplication",
            variable=self.enabled,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"]
        ).pack(side="left")
        
        # Keep mode
        keep_frame = ctk.CTkFrame(self, fg_color="transparent")
        keep_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkLabel(
            keep_frame, text="Keep:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))
        
        for val, text in [("first", "First"), ("last", "Last")]:
            ctk.CTkRadioButton(
                keep_frame, text=text, variable=self.keep_mode, value=val,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 12))
        
        # Column selection mode
        col_mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        col_mode_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkCheckBox(
            col_mode_frame, text="Use all columns for comparison",
            variable=self.use_all_columns,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            command=self._toggle_column_selection
        ).pack(side="left")
        
        # Column list (hidden when use_all_columns is True)
        self.col_list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        
        self.col_scroll = ctk.CTkScrollableFrame(
            self.col_list_frame, fg_color="transparent", height=100,
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.col_scroll.pack(fill="both", expand=True, padx=4, pady=4)
    
    def _toggle_column_selection(self):
        if self.use_all_columns.get():
            self.col_list_frame.pack_forget()
        else:
            self.col_list_frame.pack(fill="x", padx=12, pady=(0, 12))
            self._refresh_columns()
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
        self.selected_columns = set(columns)
        self._refresh_columns()
    
    def _toggle_column(self, col: str, var: BooleanVar):
        if var.get():
            self.selected_columns.add(col)
        else:
            self.selected_columns.discard(col)
    
    def _refresh_columns(self):
        for w in self.col_scroll.winfo_children():
            w.destroy()
        
        for col in self.columns:
            var = BooleanVar(value=col in self.selected_columns)
            ctk.CTkCheckBox(
                self.col_scroll, text=col, variable=var,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_primary"],
                command=lambda c=col, v=var: self._toggle_column(c, v)
            ).pack(anchor="w", pady=1)
    
    def get_config(self) -> tuple[bool, list, str]:
        columns = [] if self.use_all_columns.get() else list(self.selected_columns)
        return self.enabled.get(), columns, self.keep_mode.get()


class FilterPanel(ctk.CTkFrame):
    """Filter configuration."""
    
    OPERATORS = [
        ("equals", "Equals"),
        ("not_equals", "Not Equals"),
        ("contains", "Contains"),
        ("not_contains", "Not Contains"),
        ("starts_with", "Starts With"),
        ("ends_with", "Ends With"),
        ("is_empty", "Is Empty"),
        ("is_not_empty", "Is Not Empty"),
        ("greater_than", "Greater Than"),
        ("less_than", "Less Than"),
        ("regex", "Regex Match"),
    ]
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.filters: list[tuple[str, str, str]] = []  # (column, operator, value)
        self.logic = StringVar(value="and")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="🔍 Filters",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Logic selection
        logic_frame = ctk.CTkFrame(header, fg_color="transparent")
        logic_frame.pack(side="right")
        
        ctk.CTkRadioButton(
            logic_frame, text="AND", variable=self.logic, value="and",
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=4)
        
        ctk.CTkRadioButton(
            logic_frame, text="OR", variable=self.logic, value="or",
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left")
        
        # Filter list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.filters_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.filters_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.filters_frame,
            text="No filters defined",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(pady=10)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="+ Add Filter", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["accent_orange"],
            hover_color="#ea580c",
            corner_radius=4, command=self._add_filter
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame, text="Clear All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._clear_filters
        ).pack(side="right")
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
    
    def _add_filter(self):
        if not self.columns:
            return
        self.filters.append((self.columns[0], "contains", ""))
        self._refresh()
    
    def _remove_filter(self, idx: int):
        if 0 <= idx < len(self.filters):
            self.filters.pop(idx)
            self._refresh()
    
    def _clear_filters(self):
        self.filters.clear()
        self._refresh()
    
    def _update_filter(self, idx: int, column: str = None, operator: str = None, value: str = None):
        if 0 <= idx < len(self.filters):
            col, op, val = self.filters[idx]
            self.filters[idx] = (
                column if column is not None else col,
                operator if operator is not None else op,
                value if value is not None else val
            )
    
    def _refresh(self):
        for w in self.filters_frame.winfo_children():
            w.destroy()
        
        if not self.filters:
            self.placeholder = ctk.CTkLabel(
                self.filters_frame,
                text="No filters defined",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(pady=10)
        else:
            for idx, (col, op, val) in enumerate(self.filters):
                self._create_filter_row(idx, col, op, val)
    
    def _create_filter_row(self, idx: int, column: str, operator: str, value: str):
        frame = ctk.CTkFrame(self.filters_frame, fg_color=COLORS["bg_secondary"], corner_radius=4)
        frame.pack(fill="x", pady=2)
        
        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(6, 2))
        
        col_var = StringVar(value=column)
        ctk.CTkOptionMenu(
            row1, variable=col_var, values=self.columns,
            font=ctk.CTkFont(size=10), height=26, width=120,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_filter(i, column=v)
        ).pack(side="left", padx=(0, 4))
        
        op_display = {op: name for op, name in self.OPERATORS}
        op_var = StringVar(value=op_display.get(operator, operator))
        ctk.CTkOptionMenu(
            row1, variable=op_var, values=[name for _, name in self.OPERATORS],
            font=ctk.CTkFont(size=10), height=26, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_filter(i, operator=next((op for op, name in self.OPERATORS if name == v), v))
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            row1, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda i=idx: self._remove_filter(i)
        ).pack(side="right")
        
        # Value entry (hidden for is_empty/is_not_empty)
        if operator not in ["is_empty", "is_not_empty"]:
            row2 = ctk.CTkFrame(frame, fg_color="transparent")
            row2.pack(fill="x", padx=8, pady=(2, 6))
            
            val_entry = ctk.CTkEntry(
                row2, placeholder_text="Value...",
                font=ctk.CTkFont(size=10), height=26,
                fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"],
                text_color=COLORS["text_primary"]
            )
            val_entry.pack(fill="x")
            val_entry.insert(0, value)
            val_entry.bind("<KeyRelease>", lambda e, i=idx: self._update_filter(i, value=e.widget.get()))
    
    def get_config(self) -> tuple[list, str]:
        return self.filters.copy(), self.logic.get()


class TransformPanel(ctk.CTkFrame):
    """Data transformation configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.trim_whitespace = BooleanVar(value=True)
        self.case_transform = StringVar(value="none")
        self.empty_value = StringVar(value="")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="⚙️ Transformations",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Options
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        # Trim whitespace
        ctk.CTkCheckBox(
            opts_frame, text="Trim whitespace",
            variable=self.trim_whitespace,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=4)
        
        # Case transformation
        case_frame = ctk.CTkFrame(opts_frame, fg_color="transparent")
        case_frame.pack(fill="x", pady=8)
        
        ctk.CTkLabel(
            case_frame, text="Case:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))
        
        for val, text in [("none", "None"), ("upper", "UPPER"), ("lower", "lower"), ("title", "Title")]:
            ctk.CTkRadioButton(
                case_frame, text=text, variable=self.case_transform, value=val,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 8))
        
        # Empty value replacement
        empty_frame = ctk.CTkFrame(opts_frame, fg_color="transparent")
        empty_frame.pack(fill="x", pady=4)
        
        ctk.CTkLabel(
            empty_frame, text="Replace empty cells with:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))
        
        ctk.CTkEntry(
            empty_frame, textvariable=self.empty_value,
            placeholder_text="(leave blank)",
            font=ctk.CTkFont(size=11), height=28, width=120,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"]
        ).pack(side="left")
    
    def get_config(self) -> tuple[bool, str, str]:
        return self.trim_whitespace.get(), self.case_transform.get(), self.empty_value.get()


class OutputPanel(ctk.CTkFrame):
    """Output configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.delimiter = StringVar(value=",")
        self.encoding = StringVar(value="utf-8")
        self.quoting = StringVar(value="minimal")
        self.include_header = BooleanVar(value=True)
        self.line_ending = StringVar(value="auto")
        self.output_path = StringVar(value="")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="💾 Output Settings",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Options grid
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        # Row 1: Delimiter and Encoding
        row1 = ctk.CTkFrame(opts_frame, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        
        ctk.CTkLabel(row1, text="Delimiter:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row1, variable=self.delimiter,
            values=[",", ";", "\\t (Tab)", "|"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left", padx=(0, 20))
        
        ctk.CTkLabel(row1, text="Encoding:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row1, variable=self.encoding,
            values=["utf-8", "utf-16", "latin-1", "cp1252"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left")
        
        # Row 2: Quoting and Line ending
        row2 = ctk.CTkFrame(opts_frame, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        
        ctk.CTkLabel(row2, text="Quoting:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row2, variable=self.quoting,
            values=["minimal", "all", "nonnumeric", "none"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left", padx=(0, 20))
        
        ctk.CTkLabel(row2, text="Line End:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row2, variable=self.line_ending,
            values=["auto", "unix (LF)", "windows (CRLF)"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left")
        
        # Include header checkbox
        ctk.CTkCheckBox(
            opts_frame, text="Include header row",
            variable=self.include_header,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=(8, 4))
        
        # Output path
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkLabel(
            path_frame, text="Output File:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=(0, 4))
        
        path_row = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_row.pack(fill="x")
        
        self.path_entry = ctk.CTkEntry(
            path_row, textvariable=self.output_path,
            placeholder_text="Select output file...",
            font=ctk.CTkFont(size=11), height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"]
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        
        ctk.CTkButton(
            path_row, text="Browse", font=ctk.CTkFont(size=11),
            height=32, width=70, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=6, command=self._browse
        ).pack(side="left")
    
    def _browse(self):
        file = filedialog.asksaveasfilename(
            title="Save As",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("TSV Files", "*.tsv"), ("All Files", "*.*")]
        )
        if file:
            self.output_path.set(file)
    
    def get_config(self) -> dict:
        delim = self.delimiter.get()
        if delim == "\\t (Tab)":
            delim = "\t"
        
        line_end = self.line_ending.get()
        if "unix" in line_end:
            line_end = "unix"
        elif "windows" in line_end:
            line_end = "windows"
        else:
            line_end = "auto"
        
        return {
            "delimiter": delim,
            "encoding": self.encoding.get(),
            "quoting": self.quoting.get(),
            "include_header": self.include_header.get(),
            "line_ending": line_end,
            "output_path": self.output_path.get()
        }


class LogPanel(ctk.CTkFrame):
    """Processing log display."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📋 Processing Log",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        ctk.CTkButton(
            header, text="Clear", font=ctk.CTkFont(size=10),
            height=24, width=50, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self.clear
        ).pack(side="right")
        
        # Log text
        self.log_text = ctk.CTkTextbox(
            self, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=6
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")
    
    def log(self, message: str, level: str = "info"):
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "│", "success": "✓", "warning": "⚠", "error": "✗"}.get(level, "│")
        self.log_text.insert(END, f"[{timestamp}] {prefix} {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")
    
    def clear(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")


class StatsPanel(ctk.CTkFrame):
    """Processing statistics display."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.labels = {}
        
        stats_config = [
            ("files_processed", "Files Processed", COLORS["accent_blue"]),
            ("total_rows_read", "Rows Read", COLORS["text_primary"]),
            ("rows_filtered", "Rows Filtered", COLORS["accent_orange"]),
            ("duplicates_removed", "Duplicates Removed", COLORS["accent_purple"]),
            ("final_row_count", "Final Rows", COLORS["accent_green"]),
        ]
        
        for i, (key, label, color) in enumerate(stats_config):
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.pack(fill="x", padx=12, pady=(12 if i == 0 else 2, 2 if i < 4 else 12))
            
            ctk.CTkLabel(
                frame, text=label, font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            ).pack(side="left")
            
            val_label = ctk.CTkLabel(
                frame, text="—", font=ctk.CTkFont(size=12, weight="bold"),
                text_color=color
            )
            val_label.pack(side="right")
            self.labels[key] = val_label
    
    def update(self, stats: ProcessingStats):
        for key, label in self.labels.items():
            val = getattr(stats, key, 0)
            label.configure(text=f"{val:,}")
    
    def reset(self):
        for label in self.labels.values():
            label.configure(text="—")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class CSVPowerToolApp:
    """Main application."""
    
    def __init__(self):
        if DND_AVAILABLE:
            self.root = TkinterDnD.Tk()
        else:
            self.root = ctk.CTk()
        
        self.root.title("CSV Power Tool")
        self.root.geometry("1200x850")
        self.root.minsize(1000, 700)
        
        ctk.set_appearance_mode("dark")
        self.root.configure(bg=COLORS["bg_dark"])
        
        self.engine: CSVEngine = None
        self.processing = False
        
        self._build_ui()
        
        if DND_AVAILABLE:
            self._setup_dnd()
    
    def _build_ui(self):
        # Main container
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=16)
        
        # Header
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left")
        
        ctk.CTkLabel(
            title_frame, text="CSV Power Tool",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            title_frame, text="Combine • Filter • Transform • Deduplicate • Export",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        ).pack(anchor="w")
        
        # Version
        ver_badge = ctk.CTkFrame(header, fg_color=COLORS["bg_tertiary"], corner_radius=12)
        ver_badge.pack(side="right")
        ctk.CTkLabel(ver_badge, text="v2.0", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_muted"]).pack(padx=12, pady=4)
        
        # Content: 3 columns
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True)
        
        # Left column: Files
        left_col = ctk.CTkFrame(content, fg_color="transparent", width=280)
        left_col.pack(side="left", fill="both", padx=(0, 8))
        left_col.pack_propagate(False)
        
        self.file_panel = FileListPanel(left_col, on_change=self._on_files_changed)
        self.file_panel.pack(fill="both", expand=True)
        
        # Middle column: Configuration tabs
        mid_col = ctk.CTkFrame(content, fg_color="transparent")
        mid_col.pack(side="left", fill="both", expand=True, padx=8)
        
        self.tabview = ctk.CTkTabview(
            mid_col, fg_color=COLORS["bg_secondary"],
            segmented_button_fg_color=COLORS["bg_tertiary"],
            segmented_button_selected_color=COLORS["accent_blue"],
            segmented_button_selected_hover_color=COLORS["accent_blue_hover"],
            segmented_button_unselected_color=COLORS["bg_tertiary"],
            segmented_button_unselected_hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=8
        )
        self.tabview.pack(fill="both", expand=True)
        
        # Create tabs
        tab_columns = self.tabview.add("Columns")
        tab_sort = self.tabview.add("Sort")
        tab_dedupe = self.tabview.add("Dedupe")
        tab_filter = self.tabview.add("Filter")
        tab_transform = self.tabview.add("Transform")
        tab_output = self.tabview.add("Output")
        
        # Tab contents
        self.column_panel = ColumnPanel(tab_columns, fg_color="transparent")
        self.column_panel.pack(fill="both", expand=True)
        
        self.sort_panel = SortPanel(tab_sort, fg_color="transparent")
        self.sort_panel.pack(fill="both", expand=True)
        
        self.dedupe_panel = DedupePanel(tab_dedupe, fg_color="transparent")
        self.dedupe_panel.pack(fill="both", expand=True)
        
        self.filter_panel = FilterPanel(tab_filter, fg_color="transparent")
        self.filter_panel.pack(fill="both", expand=True)
        
        self.transform_panel = TransformPanel(tab_transform, fg_color="transparent")
        self.transform_panel.pack(fill="both", expand=True)
        
        self.output_panel = OutputPanel(tab_output, fg_color="transparent")
        self.output_panel.pack(fill="both", expand=True)
        
        # Right column: Log and Stats
        right_col = ctk.CTkFrame(content, fg_color="transparent", width=300)
        right_col.pack(side="right", fill="both", padx=(8, 0))
        right_col.pack_propagate(False)
        
        self.stats_panel = StatsPanel(right_col)
        self.stats_panel.pack(fill="x", pady=(0, 8))
        
        self.log_panel = LogPanel(right_col)
        self.log_panel.pack(fill="both", expand=True)
        
        # Bottom: Progress and buttons
        bottom = ctk.CTkFrame(main, fg_color="transparent")
        bottom.pack(fill="x", pady=(12, 0))
        
        # Progress
        progress_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(0, 8))
        
        self.progress_label = ctk.CTkLabel(
            progress_frame, text="Ready",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.progress_label.pack(anchor="w", pady=(0, 4))
        
        self.progress_bar = ctk.CTkProgressBar(
            progress_frame, height=8,
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"],
            corner_radius=4
        )
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)
        
        # Buttons
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(fill="x")
        
        # Preset buttons
        preset_frame = ctk.CTkFrame(btn_frame, fg_color="transparent")
        preset_frame.pack(side="left")
        
        ctk.CTkButton(
            preset_frame, text="💾 Save Config", font=ctk.CTkFont(size=11),
            height=36, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._save_config
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            preset_frame, text="📂 Load Config", font=ctk.CTkFont(size=11),
            height=36, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._load_config
        ).pack(side="left")
        
        # Action buttons
        action_frame = ctk.CTkFrame(btn_frame, fg_color="transparent")
        action_frame.pack(side="right")
        
        self.cancel_btn = ctk.CTkButton(
            action_frame, text="Cancel", font=ctk.CTkFont(size=13),
            height=42, width=100, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_primary"],
            corner_radius=8, command=self._cancel,
            state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))
        
        self.process_btn = ctk.CTkButton(
            action_frame, text="▶  Process Files",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42, width=160,
            fg_color=COLORS["accent_green"],
            hover_color=COLORS["accent_green_hover"],
            corner_radius=8, command=self._process
        )
        self.process_btn.pack(side="left")
    
    def _setup_dnd(self):
        def drop(event):
            files = self.root.tk.splitlist(event.data)
            added = self.file_panel.add_files([Path(f) for f in files])
            if added:
                self.log_panel.log(f"Added {added} file(s) via drag & drop", "info")
        
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', drop)
    
    def _on_files_changed(self):
        if self.file_panel.files:
            columns = CSVEngine(ProcessingConfig()).discover_columns(self.file_panel.files)
            self.column_panel.set_columns(columns)
            self.sort_panel.set_columns(columns)
            self.dedupe_panel.set_columns(columns)
            self.filter_panel.set_columns(columns)
    
    def _update_progress(self, value: float, status: str):
        self.progress_bar.set(value / 100)
        self.progress_label.configure(text=status)
        self.root.update_idletasks()
    
    def _build_config(self) -> ProcessingConfig:
        config = ProcessingConfig()
        
        # Columns
        mode, selected = self.column_panel.get_config()
        config.columns_mode = mode
        config.selected_columns = selected
        
        # Sort
        enabled, rules, case_sens, numeric = self.sort_panel.get_config()
        config.sort_enabled = enabled
        config.sort_columns = rules
        config.sort_case_sensitive = case_sens
        config.sort_numeric_aware = numeric
        
        # Dedupe
        enabled, columns, keep = self.dedupe_panel.get_config()
        config.dedupe_enabled = enabled
        config.dedupe_columns = columns
        config.dedupe_keep = keep
        
        # Filter
        filters, logic = self.filter_panel.get_config()
        config.filters = filters
        config.filter_logic = logic
        
        # Transform
        trim, case, empty = self.transform_panel.get_config()
        config.trim_whitespace = trim
        config.case_transform = case
        config.empty_value = empty
        
        # Output
        output_config = self.output_panel.get_config()
        config.output_delimiter = output_config["delimiter"]
        config.output_encoding = output_config["encoding"]
        config.output_quoting = output_config["quoting"]
        config.include_header = output_config["include_header"]
        config.line_ending = output_config["line_ending"]
        
        return config
    
    def _process(self):
        if not self.file_panel.files:
            self.log_panel.log("No files selected", "error")
            return
        
        output_config = self.output_panel.get_config()
        output_path = output_config["output_path"]
        
        if not output_path:
            output_path = str(Path.home() / "combined_output.csv")
            self.output_panel.output_path.set(output_path)
        
        self.processing = True
        self._set_ui_state(True)
        self.stats_panel.reset()
        self.progress_bar.set(0)
        self.log_panel.clear()
        self.log_panel.log("Starting processing...", "info")
        
        config = self._build_config()
        
        def run():
            self.engine = CSVEngine(
                config,
                progress_callback=lambda v, s: self.root.after(0, lambda: self._update_progress(v, s)),
                log_callback=lambda m, l: self.root.after(0, lambda: self.log_panel.log(m, l))
            )
            
            stats = self.engine.process(self.file_panel.files, Path(output_path))
            self.root.after(0, lambda: self._complete(stats))
        
        threading.Thread(target=run, daemon=True).start()
    
    def _cancel(self):
        if self.engine:
            self.engine.cancel()
            self.log_panel.log("Cancelling...", "warning")
    
    def _complete(self, stats: ProcessingStats):
        self.processing = False
        self._set_ui_state(False)
        self.stats_panel.update(stats)
        
        if stats.final_row_count > 0:
            self.log_panel.log(f"✓ Complete! {stats.final_row_count:,} rows saved", "success")
        else:
            self.log_panel.log("Processing completed with no output", "warning")
    
    def _set_ui_state(self, processing: bool):
        state = "disabled" if processing else "normal"
        self.process_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if processing else "disabled")
    
    def _save_config(self):
        file = filedialog.asksaveasfilename(
            title="Save Configuration",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")]
        )
        if file:
            config = self._build_config()
            data = {
                "columns_mode": config.columns_mode,
                "selected_columns": config.selected_columns,
                "sort_enabled": config.sort_enabled,
                "sort_columns": config.sort_columns,
                "sort_case_sensitive": config.sort_case_sensitive,
                "sort_numeric_aware": config.sort_numeric_aware,
                "dedupe_enabled": config.dedupe_enabled,
                "dedupe_columns": config.dedupe_columns,
                "dedupe_keep": config.dedupe_keep,
                "filters": config.filters,
                "filter_logic": config.filter_logic,
                "trim_whitespace": config.trim_whitespace,
                "case_transform": config.case_transform,
                "empty_value": config.empty_value,
                "output_delimiter": config.output_delimiter,
                "output_encoding": config.output_encoding,
                "output_quoting": config.output_quoting,
                "include_header": config.include_header,
                "line_ending": config.line_ending,
            }
            with open(file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log_panel.log(f"Configuration saved: {Path(file).name}", "success")
    
    def _load_config(self):
        file = filedialog.askopenfilename(
            title="Load Configuration",
            filetypes=[("JSON Files", "*.json")]
        )
        if file:
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                
                # Apply to panels
                self.column_panel.mode.set(data.get("columns_mode", "all"))
                self.column_panel.selected = set(data.get("selected_columns", []))
                
                self.sort_panel.enabled.set(data.get("sort_enabled", False))
                self.sort_panel.sort_rules = data.get("sort_columns", [])
                self.sort_panel.case_sensitive.set(data.get("sort_case_sensitive", False))
                self.sort_panel.numeric_aware.set(data.get("sort_numeric_aware", True))
                self.sort_panel._refresh_rules()
                
                self.dedupe_panel.enabled.set(data.get("dedupe_enabled", True))
                self.dedupe_panel.selected_columns = set(data.get("dedupe_columns", []))
                self.dedupe_panel.keep_mode.set(data.get("dedupe_keep", "first"))
                
                self.filter_panel.filters = data.get("filters", [])
                self.filter_panel.logic.set(data.get("filter_logic", "and"))
                self.filter_panel._refresh()
                
                self.transform_panel.trim_whitespace.set(data.get("trim_whitespace", True))
                self.transform_panel.case_transform.set(data.get("case_transform", "none"))
                self.transform_panel.empty_value.set(data.get("empty_value", ""))
                
                delim = data.get("output_delimiter", ",")
                if delim == "\t":
                    delim = "\\t (Tab)"
                self.output_panel.delimiter.set(delim)
                self.output_panel.encoding.set(data.get("output_encoding", "utf-8"))
                self.output_panel.quoting.set(data.get("output_quoting", "minimal"))
                self.output_panel.include_header.set(data.get("include_header", True))
                
                self.log_panel.log(f"Configuration loaded: {Path(file).name}", "success")
                
            except Exception as e:
                self.log_panel.log(f"Error loading config: {e}", "error")
    
    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = CSVPowerToolApp()
    app.run()
