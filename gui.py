"""Tkinter interface and same-executable headless dispatcher."""

from __future__ import annotations

import json
from pathlib import Path
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

import batch_comfy


def _attach_parent_console() -> None:
    """Attach the windowed executable to a caller's console for headless output."""
    if os.name != "nt" or not getattr(sys, "frozen", False) or sys.stdout is not None:
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except (OSError, AttributeError):
        return


def _hide_private_console() -> None:
    """Hide the console created by an Explorer double-click, but never a caller's console."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        process_ids = (ctypes.c_ulong * 2)()
        process_count = ctypes.windll.kernel32.GetConsoleProcessList(process_ids, 2)
        if process_count == 1:
            window = ctypes.windll.kernel32.GetConsoleWindow()
            if window:
                ctypes.windll.user32.ShowWindow(window, 0)
    except (OSError, AttributeError):
        return


class ToolTip:
    """Delayed, screen-clamped tooltip which may be placed above large widgets."""

    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 700, placement: str = "below"):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.placement = placement
        self.tipwindow: tk.Toplevel | None = None
        self.after_id: str | None = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.cancel()
        if self.text:
            self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self):
        self.after_id = None
        if self.tipwindow or not self.text or not self.widget.winfo_exists():
            return
        self.tipwindow = tk.Toplevel(self.widget)
        self.tipwindow.wm_overrideredirect(True)
        label = tk.Label(
            self.tipwindow,
            text=self.text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            wraplength=440,
        )
        label.pack(ipadx=5, ipady=3)
        self.tipwindow.update_idletasks()
        width, height = self.tipwindow.winfo_reqwidth(), self.tipwindow.winfo_reqheight()
        x = min(self.widget.winfo_rootx() + 20, self.widget.winfo_screenwidth() - width - 8)
        if self.placement == "above":
            y = self.widget.winfo_rooty() - height - 8
        else:
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        y = max(8, min(y, self.widget.winfo_screenheight() - height - 8))
        self.tipwindow.wm_geometry(f"+{x}+{y}")

    def hide(self, _event=None):
        self.cancel()
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


class ParameterDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, initial: dict[str, Any] | None = None):
        super().__init__(parent)
        self.title("Workflow Parameter")
        self.resizable(False, False)
        self.result: dict[str, Any] | None = None
        initial = initial or {"node": "", "input": "value", "value": ""}
        self.node_var = tk.StringVar(value=str(initial.get("node", "")))
        self.input_var = tk.StringVar(value=str(initial.get("input", "value")))
        self.type_var = tk.StringVar(value=self.type_name(initial.get("value")))
        self.value_var = tk.StringVar(value=self.display_value(initial.get("value")))
        body = ttk.Frame(self, padding=12)
        body.grid(sticky="nsew")
        for row, (label, variable) in enumerate((
            ("Node title", self.node_var), ("Input name", self.input_var), ("Value", self.value_var)
        )):
            ttk.Label(body, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=variable, width=48).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(body, text="Data type:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(
            body,
            textvariable=self.type_var,
            values=("string", "integer", "number", "boolean", "null", "json"),
            state="readonly",
            width=45,
        ).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(
            body,
            text="Use the node's exact displayed title. Prompt, OutputPath, CharacterName, and Seed are automatic.",
            foreground="#555555",
            wraplength=400,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 10))
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="OK", command=self.accept).pack(side="right")
        self.bind("<Return>", lambda _event: self.accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.wait_visibility()
        self.focus_force()

    @staticmethod
    def type_name(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, (dict, list)):
            return "json"
        return "string"

    @staticmethod
    def display_value(value: Any) -> str:
        if isinstance(value, (dict, list, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def accept(self):
        node, input_name = self.node_var.get().strip(), self.input_var.get().strip()
        if not node or not input_name:
            messagebox.showerror("Invalid parameter", "Node title and input name are required.", parent=self)
            return
        raw, value_type = self.value_var.get(), self.type_var.get()
        try:
            if value_type == "string":
                value: Any = raw
            elif value_type == "integer":
                value = int(raw)
            elif value_type == "number":
                value = float(raw)
            elif value_type == "boolean":
                if raw.strip().lower() not in {"true", "false"}:
                    raise ValueError("enter true or false")
                value = raw.strip().lower() == "true"
            elif value_type == "null":
                value = None
            else:
                value = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Invalid value", f"The {value_type} value is invalid: {exc}", parent=self)
            return
        try:
            batch_comfy.normalize_parameters([{**{"node": node, "input": input_name}, "value": value}])
        except batch_comfy.ConfigurationError as exc:
            messagebox.showerror("Invalid parameter", str(exc), parent=self)
            return
        self.result = {"node": node, "input": input_name, "value": value}
        self.destroy()


class BatcherApp:
    CUSTOM_PRESET = "Custom"

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ComfyUI Batch Caller")
        root.geometry("820x820")
        root.minsize(720, 700)
        self.parameters: list[dict[str, Any]] = []
        self.presets: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] | None = None
        self.settings_error: str | None = None
        self.comfy_process = None
        self._build_ui()
        self._load_preset_index()
        self.root.protocol("WM_DELETE_WINDOW", self._close_window)
        self.root.after(100, self._initialize_global_settings)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        connection = ttk.LabelFrame(outer, text="ComfyUI connection", padding=10)
        connection.pack(fill="x")
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="Status:").grid(row=0, column=0, sticky="w")
        self.connection_var = tk.StringVar(value="Not connected")
        self.connection_label = tk.Label(connection, textvariable=self.connection_var, fg="#b42318", anchor="w")
        self.connection_label.grid(row=0, column=1, sticky="w", padx=8)
        connection_buttons = ttk.Frame(connection)
        connection_buttons.grid(row=0, column=2)
        self.connect_button = ttk.Button(connection_buttons, text="Start ComfyUI", command=self.start_comfy)
        self.connect_button.pack(side="left")
        self.stop_connection_button = ttk.Button(
            connection_buttons, text="Stop ComfyUI", command=self.stop_comfy, state="disabled"
        )
        self.stop_connection_button.pack(side="left", padx=(6, 0))
        ToolTip(self.connection_label, "Connection to the ComfyUI server configured in global_settings.json.")
        ToolTip(self.connect_button, "Launch the configured portable ComfyUI GPU batch file and wait until its API is ready.")
        ToolTip(self.stop_connection_button, "Stop only the ComfyUI process started by this batcher and release its model memory.")
        ttk.Label(connection, text="Console log:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.comfy_log_var = tk.StringVar()
        comfy_log_entry = ttk.Entry(connection, textvariable=self.comfy_log_var, state="readonly")
        comfy_log_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=(6, 0))
        copy_log_button = ttk.Button(
            connection, text="Copy path", command=lambda: self._copy_text(self.comfy_log_var.get())
        )
        copy_log_button.grid(row=1, column=2, pady=(6, 0))
        ToolTip(comfy_log_entry, "ComfyUI's complete console output is saved here, including node validation errors.")
        ToolTip(copy_log_button, "Copy the ComfyUI console-log path to the clipboard.")

        batch_group = ttk.LabelFrame(outer, text="Batch", padding=10)
        batch_group.pack(fill="x", pady=(10, 0))
        batch_group.columnconfigure(1, weight=1)
        self.config_var = tk.StringVar()
        self.output_name_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self._file_row(
            batch_group, 0, "Prompt config:", self.config_var,
            lambda: self._browse_file(self.config_var),
            "Batch JSON with base_additional_prompt and characters containing name, prompt, and runs.",
        )
        ttk.Label(batch_group, text="Output name:").grid(row=1, column=0, sticky="w", pady=4)
        output_name = ttk.Entry(batch_group, textvariable=self.output_name_var)
        output_name.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        ToolTip(output_name, "One new folder name, such as Underwater. It is created under ComfyUI/output when the batch starts.")
        ttk.Label(batch_group, text="Output path:").grid(row=2, column=0, sticky="w", pady=4)
        output_path = ttk.Entry(batch_group, textvariable=self.output_path_var, state="readonly")
        output_path.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        copy_button = ttk.Button(batch_group, text="Copy", command=self._copy_output_path)
        copy_button.grid(row=2, column=2, pady=4)
        ToolTip(output_path, "Generated from the configured ComfyUI output folder and the output name above.")
        ToolTip(copy_button, "Copy the complete generated output path to the clipboard.")
        self.output_name_var.trace_add("write", self._update_output_path)

        group = ttk.LabelFrame(outer, text="Workflow preset and parameters", padding=10)
        group.pack(fill="both", expand=True, pady=(10, 0))
        group.columnconfigure(1, weight=1)
        group.rowconfigure(3, weight=1)
        ttk.Label(group, text="Preset:").grid(row=0, column=0, sticky="w", pady=4)
        self.preset_var = tk.StringVar(value=self.CUSTOM_PRESET)
        self.preset_combo = ttk.Combobox(group, textvariable=self.preset_var, state="readonly")
        self.preset_combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        self.preset_combo.bind("<<ComboboxSelected>>", self._preset_selected)
        ToolTip(self.preset_combo,
                "Presets select a workflow and can optionally load a parameter map. "
                "A preset without parameters clears the map.")
        self.workflow_var = tk.StringVar()
        self._file_row(
            group, 1, "Workflow:", self.workflow_var,
            lambda: self._browse_file(self.workflow_var, True),
            "API-format workflow with titled Prompt, OutputPath, and CharacterName nodes. Seed is optional and automatic.",
        )
        toolbar = ttk.Frame(group)
        toolbar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(toolbar, text="Parameter map").pack(side="left")
        load_button = ttk.Button(toolbar, text="📂 Load", command=self._load_parameter_file)
        save_button = ttk.Button(toolbar, text="💾 Save", command=self._save_parameter_file)
        load_button.pack(side="right", padx=(6, 0))
        save_button.pack(side="right", padx=(6, 0))
        ToolTip(load_button, "Load a reusable JSON parameter map from disk.")
        ToolTip(save_button, "Save this map for reuse, presets, or headless mode.")
        columns = ("node", "input", "type", "value")
        self.parameter_tree = ttk.Treeview(group, columns=columns, show="headings", height=9)
        for column, label, width in (
            ("node", "Node title", 210), ("input", "Input", 90),
            ("type", "Type", 70), ("value", "Value", 290),
        ):
            self.parameter_tree.heading(column, text=label)
            self.parameter_tree.column(column, width=width, minwidth=55)
        self.parameter_tree.grid(row=3, column=0, columnspan=3, sticky="nsew")
        self.parameter_tree.bind("<Double-1>", lambda _event: self._edit_parameter())
        ToolTip(
            self.parameter_tree,
            "Rows match workflow nodes by displayed title and replace one input while preserving its data type.",
            placement="above",
        )
        controls = ttk.Frame(group)
        controls.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for text, command, tip in (
            ("Add", self._add_parameter, "Add an override."),
            ("Edit", self._edit_parameter, "Edit the selected override."),
            ("Remove", self._remove_parameter, "Remove the selected override."),
            ("Clear", self._clear_parameters, "Remove all overrides."),
        ):
            button = ttk.Button(controls, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            ToolTip(button, tip)
        self.start_button = ttk.Button(outer, text="Start Batch", command=self.start_batch)
        self.start_button.pack(pady=12)
        ToolTip(self.start_button, "Create the output folder, validate inputs, and queue every run.")
        ttk.Label(outer, text="Logs:").pack(anchor="w")
        self.log_box = scrolledtext.ScrolledText(
            outer, height=9, wrap=tk.WORD, bg="#1e1e1e", fg="white", insertbackground="white"
        )
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))

    def _file_row(self, parent, row, label, variable, command, tooltip):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        button = ttk.Button(parent, text="Browse…", command=command)
        button.grid(row=row, column=2, pady=4)
        parent.columnconfigure(1, weight=1)
        ToolTip(entry, tooltip)
        ToolTip(button, tooltip)

    def _initialize_global_settings(self):
        path = batch_comfy.application_directory() / batch_comfy.DEFAULT_GLOBAL_SETTINGS_FILE
        try:
            self.settings = batch_comfy.load_global_settings(path)
            self.settings_error = None
            self.comfy_log_var.set(self.settings["comfy_log_file"])
        except (OSError, batch_comfy.ConfigurationError) as exc:
            self.settings = None
            self.settings_error = str(exc)
            self._set_connection_status("Configuration error", connected=False)
            self.connect_button.config(state="disabled")
            self.start_button.config(state="disabled")
            messagebox.showerror("Invalid global settings", str(exc), parent=self.root)
            return
        self._update_output_path()
        if batch_comfy.check_comfy_connection(self.settings["server_url"]):
            if batch_comfy.managed_comfy_pid(self.settings) is not None:
                self._set_connection_status("Connected (batcher managed)", connected=True)
                self.stop_connection_button.config(state="normal")
            else:
                self._set_connection_status("Connected (external)", connected=True)
        else:
            self._set_connection_status("Not connected", connected=False)

    def _set_connection_status(self, text: str, *, connected: bool):
        self.connection_var.set(text)
        self.connection_label.config(fg="#067647" if connected else "#b42318")

    def start_comfy(self):
        if self.settings is None:
            messagebox.showerror("Invalid global settings", self.settings_error or "Global settings are unavailable.")
            return
        self.connect_button.config(state="disabled")
        self._set_connection_status("Starting…", connected=False)
        self.log("Starting ComfyUI and waiting for its API...")

        def run():
            try:
                process = batch_comfy.start_comfy_and_wait(self.settings, self.log)
            except Exception as exc:
                self.log(f"Connection error: {exc}")
                self.root.after(0, lambda: self._set_connection_status("Not connected", connected=False))
                self.root.after(0, lambda: messagebox.showerror("Cannot start ComfyUI", str(exc), parent=self.root))
            else:
                self.comfy_process = process
                managed = process is not None or batch_comfy.managed_comfy_pid(self.settings) is not None
                label = "Connected (batcher managed)" if managed else "Connected (external)"
                self.root.after(0, lambda: self._set_connection_status(label, connected=True))
                if managed:
                    self.root.after(0, lambda: self.stop_connection_button.config(state="normal"))
            finally:
                self.root.after(0, lambda: self.connect_button.config(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def stop_comfy(self, *, close_after: bool = False):
        if self.settings is None:
            return
        if self.comfy_process is None and batch_comfy.managed_comfy_pid(self.settings) is None:
            messagebox.showinfo(
                "External ComfyUI",
                "This server was not started by the batcher, so it will not be stopped automatically.",
                parent=self.root,
            )
            return
        self.connect_button.config(state="disabled")
        self.stop_connection_button.config(state="disabled")
        self._set_connection_status("Stopping…", connected=False)

        def run():
            try:
                stopped = batch_comfy.stop_managed_comfy(self.settings, self.comfy_process, self.log)
            except Exception as exc:
                stopped = False
                self.log(f"Stop error: {exc}")
                self.root.after(0, lambda: messagebox.showerror("Cannot stop ComfyUI", str(exc), parent=self.root))
            self.comfy_process = None
            if close_after:
                self.root.after(0, self.root.destroy)
                return
            status = "Not connected" if stopped else "Connected (external)"
            self.root.after(0, lambda: self._set_connection_status(status, connected=not stopped))
            self.root.after(0, lambda: self.connect_button.config(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _close_window(self):
        managed = self.settings is not None and (
            self.comfy_process is not None or batch_comfy.managed_comfy_pid(self.settings) is not None
        )
        if not managed:
            self.root.destroy()
            return
        should_stop = messagebox.askyesno(
            "Stop ComfyUI?",
            "ComfyUI was started by this batcher. Stop it and release its GPU/RAM memory before closing?",
            parent=self.root,
            default=messagebox.YES,
        )
        if should_stop:
            self.stop_comfy(close_after=True)
        else:
            self.root.destroy()

    def _update_output_path(self, *_args):
        if self.settings is None:
            self.output_path_var.set("")
            return
        name = self.output_name_var.get().strip()
        if not name:
            self.output_path_var.set(str(self.settings["output_folder"]))
            return
        try:
            self.output_path_var.set(str(batch_comfy.output_path_for(self.settings, name)))
        except batch_comfy.ConfigurationError:
            self.output_path_var.set("Invalid output folder name")

    def _copy_output_path(self):
        value = self.output_path_var.get()
        if not value or value == "Invalid output folder name":
            return
        self._copy_text(value)

    def _copy_text(self, value: str):
        if not value:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update()

    def _browse_file(self, variable, mark_custom=False):
        selected = filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if selected:
            variable.set(selected)
            if mark_custom:
                self.preset_var.set(self.CUSTOM_PRESET)

    def _load_preset_index(self):
        path = batch_comfy.application_directory() / batch_comfy.DEFAULT_PRESET_FILE
        try:
            loaded = batch_comfy.load_presets(path) if path.exists() else []
        except (OSError, batch_comfy.ConfigurationError) as exc:
            messagebox.showwarning("Preset file error", str(exc), parent=self.root)
            loaded = []
        self.presets = {item["name"]: item for item in loaded}
        self.preset_combo["values"] = [self.CUSTOM_PRESET, *self.presets]

    def _preset_selected(self, _event=None):
        name = self.preset_var.get()
        if name == self.CUSTOM_PRESET:
            return
        preset = self.presets[name]
        try:
            parameters = (batch_comfy.load_parameters(preset["parameters"])
                          if preset["parameters"] else [])
        except (OSError, batch_comfy.ConfigurationError) as exc:
            messagebox.showerror("Cannot load preset", str(exc), parent=self.root)
            self.preset_var.set(self.CUSTOM_PRESET)
            return
        self.workflow_var.set(preset["workflow"])
        self.parameters = parameters
        self._refresh_parameters()

    def _refresh_parameters(self):
        self.parameter_tree.delete(*self.parameter_tree.get_children())
        for index, item in enumerate(self.parameters):
            value = item["value"]
            self.parameter_tree.insert("", "end", iid=str(index), values=(
                item["node"], item["input"], ParameterDialog.type_name(value), ParameterDialog.display_value(value)
            ))

    def _add_parameter(self):
        dialog = ParameterDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result:
            self.parameters.append(dialog.result)
            self.preset_var.set(self.CUSTOM_PRESET)
            self._refresh_parameters()

    def _selected_index(self):
        selected = self.parameter_tree.selection()
        if not selected:
            messagebox.showinfo("Select a parameter", "Select a parameter row first.", parent=self.root)
            return None
        return int(selected[0])

    def _edit_parameter(self):
        index = self._selected_index()
        if index is None:
            return
        dialog = ParameterDialog(self.root, self.parameters[index])
        self.root.wait_window(dialog)
        if dialog.result:
            self.parameters[index] = dialog.result
            self.preset_var.set(self.CUSTOM_PRESET)
            self._refresh_parameters()

    def _remove_parameter(self):
        index = self._selected_index()
        if index is not None:
            del self.parameters[index]
            self.preset_var.set(self.CUSTOM_PRESET)
            self._refresh_parameters()

    def _clear_parameters(self):
        self.parameters.clear()
        self.preset_var.set(self.CUSTOM_PRESET)
        self._refresh_parameters()

    def _load_parameter_file(self):
        selected = filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not selected:
            return
        try:
            self.parameters = batch_comfy.load_parameters(selected)
        except (OSError, batch_comfy.ConfigurationError) as exc:
            messagebox.showerror("Cannot load parameters", str(exc), parent=self.root)
            return
        self.preset_var.set(self.CUSTOM_PRESET)
        self._refresh_parameters()

    def _save_parameter_file(self):
        selected = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if not selected:
            return
        try:
            with Path(selected).open("w", encoding="utf-8") as handle:
                json.dump(batch_comfy.parameters_to_json(self.parameters), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError as exc:
            messagebox.showerror("Cannot save parameters", str(exc), parent=self.root)

    def log(self, message: str):
        def append():
            self.log_box.insert(tk.END, message + "\n")
            self.log_box.see(tk.END)
        self.root.after(0, append)

    def start_batch(self):
        if self.settings is None:
            messagebox.showerror("Invalid global settings", self.settings_error or "Global settings are unavailable.")
            return
        config, workflow = self.config_var.get().strip(), self.workflow_var.get().strip()
        if not config or not workflow or not self.output_name_var.get().strip():
            messagebox.showerror(
                "Missing input", "Select a prompt config and workflow, then enter an output folder name.", parent=self.root
            )
            return
        if not batch_comfy.check_comfy_connection(self.settings["server_url"]):
            self._set_connection_status("Not connected", connected=False)
            messagebox.showerror("Not connected", "Start ComfyUI and wait for the status to show Connected.", parent=self.root)
            return
        try:
            output = batch_comfy.output_path_for(self.settings, self.output_name_var.get())
            output.mkdir(parents=True, exist_ok=True)
        except (OSError, batch_comfy.ConfigurationError) as exc:
            messagebox.showerror("Invalid output folder", str(exc), parent=self.root)
            return
        parameters = [dict(item) for item in self.parameters]
        self.start_button.config(state="disabled")
        self.log_box.delete("1.0", tk.END)
        self.log(f"Output folder: {output}")

        def run():
            try:
                batch_comfy.queue_workflow(
                    config, workflow, output, parameters,
                    comfy_api=self.settings["server_url"], log_func=self.log,
                )
            except Exception as exc:
                self.log(f"Error: {exc}")
                self.root.after(0, lambda: messagebox.showerror("Batch failed", str(exc), parent=self.root))
            finally:
                self.root.after(0, lambda: self.start_button.config(state="normal"))

        threading.Thread(target=run, daemon=True).start()


def main() -> int:
    if len(sys.argv) > 1:
        _attach_parent_console()
        return batch_comfy.main(sys.argv[1:])
    _hide_private_console()
    root = tk.Tk()
    BatcherApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
