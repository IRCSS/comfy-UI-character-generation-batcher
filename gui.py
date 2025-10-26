import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import threading
import os
import batch_comfy


import tkinter as tk

class ToolTip:
    """Simple tooltip for Tkinter widgets."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Segoe UI", 9)
        )
        label.pack(ipadx=4, ipady=2)

    def hide(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None

def start_batch():
    config_path = config_entry.get()
    workflow_path = workflow_entry.get()
    output_path = output_entry.get()

    if not config_path or not workflow_path:
        messagebox.showerror("Error", "Please select both config and workflow files.")
        return

    if not output_path:
        output_path = os.getcwd()

    # Disable the button while running
    start_button.config(state="disabled")
    log_box.delete(1.0, tk.END)
    log_box.insert(tk.END, "🚀 Starting batch process...\n")

    def run_batch():
        try:
            batch_comfy.queue_workflow(config_path, workflow_path, output_path, log_func=log)
            log("\n✅ All jobs queued successfully!")
        except Exception as e:
            log(f"\n❌ Error: {e}")
            messagebox.showerror("Error", str(e))
        finally:
            start_button.config(state="normal")

    threading.Thread(target=run_batch, daemon=True).start()


def log(message):
    log_box.insert(tk.END, message + "\n")
    log_box.see(tk.END)


# --- GUI Setup ---
root = tk.Tk()
root.title("ComfyUI Batch Generator")
root.geometry("600x500")

# Config file selector
tk.Label(root, text="Config File:").pack(anchor="w", padx=10, pady=(10, 0))
config_entry = tk.Entry(root, width=70)
config_entry.pack(padx=10)
tk.Button(root, text="Browse", command=lambda: config_entry.insert(0, filedialog.askopenfilename(filetypes=[("JSON files", "*.json")]))).pack(padx=10, pady=5)
ToolTip(config_entry,
        "Select your config.json file.\nIt should contain the list of characters, prompts, and runs.")

# Workflow file selector
tk.Label(root, text="Workflow File:").pack(anchor="w", padx=10, pady=(10, 0))
workflow_entry = tk.Entry(root, width=70)
workflow_entry.pack(padx=10)
tk.Button(root, text="Browse", command=lambda: workflow_entry.insert(0, filedialog.askopenfilename(filetypes=[("JSON files", "*.json")]))).pack(padx=10, pady=5)
ToolTip(workflow_entry,
        "Select your ComfyUI workflow file exported in API format. \nMake sure dev option is enabled in Comfy for exporting API format. \nExample: TextTo3DCharacter.json")


# Output folder
tk.Label(root, text="Output Folder:").pack(anchor="w", padx=10, pady=(10, 0))
output_entry = tk.Entry(root, width=70)
output_entry.pack(padx=10)
tk.Button(root, text="Browse", command=lambda: output_entry.insert(0, filedialog.askdirectory())).pack(padx=10, pady=5)
ToolTip(output_entry,
        "Choose where generated outputs (images/models) will be saved. \nComfy only accepts sub folders in its Output folder")
# Start button
start_button = tk.Button(root, text="Start Batch", command=start_batch, bg="#4CAF50", fg="white", width=20)
start_button.pack(pady=15)

# Log box
tk.Label(root, text="Logs:").pack(anchor="w", padx=10)
log_box = scrolledtext.ScrolledText(root, width=70, height=15, wrap=tk.WORD, bg="#1e1e1e", fg="white", insertbackground="white")
log_box.pack(padx=10, pady=5)

root.mainloop()