"""Core batching, validation, preset loading, and command-line interface."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping

import requests

DEFAULT_COMFY_API = "http://127.0.0.1:8188"
DEFAULT_PRESET_FILE = "presets.json"
DEFAULT_GLOBAL_SETTINGS_FILE = "global_settings.json"
RESERVED_NODE_TITLES = {"prompt", "outputpath", "charactername", "seed"}
# Kept deliberately small so the same value is accepted by ordinary 32-bit
# integer nodes as well as ComfyUI's larger seed-specific inputs.
MAX_AUTOMATIC_SEED = 999_999


class ConfigurationError(ValueError):
    """Raised when a user-supplied JSON file or option is invalid."""


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_json(path: os.PathLike[str] | str, description: str) -> Any:
    json_path = Path(path).expanduser()
    if not json_path.is_file():
        raise FileNotFoundError(f"{description} not found: {json_path}")
    try:
        with json_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in {description.lower()} '{json_path}' at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


def load_global_settings(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Load and validate the portable ComfyUI installation settings."""
    settings_path = Path(path).expanduser().resolve()
    raw = _load_json(settings_path, "Global settings file")
    if not isinstance(raw, dict):
        raise ConfigurationError("Global settings must be a JSON object.")

    parent_value = raw.get("comfy_parent_path")
    if not isinstance(parent_value, str) or not parent_value.strip():
        raise ConfigurationError("Global settings requires a non-empty 'comfy_parent_path'.")
    parent = Path(parent_value).expanduser().resolve()
    if not parent.is_dir():
        raise ConfigurationError(f"ComfyUI parent folder not found: {parent}")
    comfy_folder = parent / "ComfyUI"
    if not comfy_folder.is_dir() or not (comfy_folder / "main.py").is_file():
        raise ConfigurationError(
            f"'{parent}' is not a portable ComfyUI parent folder: expected ComfyUI\\main.py."
        )

    launcher_value = raw.get("launcher", "run_nvidia_gpu.bat")
    if not isinstance(launcher_value, str) or not launcher_value.strip():
        raise ConfigurationError("Global setting 'launcher' must be a non-empty path string.")
    launcher = Path(launcher_value).expanduser()
    if not launcher.is_absolute():
        launcher = parent / launcher
    launcher = launcher.resolve()
    if not launcher.is_file():
        raise ConfigurationError(f"ComfyUI launcher not found: {launcher}")

    server_url = raw.get("server_url", DEFAULT_COMFY_API)
    if not isinstance(server_url, str) or not server_url.startswith(("http://", "https://")):
        raise ConfigurationError("Global setting 'server_url' must be an http:// or https:// URL.")
    timeout = raw.get("startup_timeout_seconds", 300)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigurationError("'startup_timeout_seconds' must be a positive number.")

    log_value = raw.get("comfy_log_file", "comfyui_console.log")
    if not isinstance(log_value, str) or not log_value.strip():
        raise ConfigurationError("Global setting 'comfy_log_file' must be a non-empty path string.")
    log_path = Path(log_value).expanduser()
    if not log_path.is_absolute():
        log_path = settings_path.parent / log_path
    log_path = log_path.resolve()
    pid_value = raw.get("comfy_pid_file", "comfyui_managed.pid")
    if not isinstance(pid_value, str) or not pid_value.strip():
        raise ConfigurationError("Global setting 'comfy_pid_file' must be a non-empty path string.")
    pid_path = Path(pid_value).expanduser()
    if not pid_path.is_absolute():
        pid_path = settings_path.parent / pid_path
    pid_path = pid_path.resolve()

    return {
        **raw,
        "settings_path": str(settings_path),
        "comfy_parent_path": str(parent),
        "comfy_folder": str(comfy_folder),
        "output_folder": str((comfy_folder / "output").resolve()),
        "launcher": str(launcher),
        "server_url": server_url.rstrip("/"),
        "startup_timeout_seconds": float(timeout),
        "comfy_log_file": str(log_path),
        "comfy_pid_file": str(pid_path),
    }


def check_comfy_connection(server_url: str, timeout: float = 3.0) -> bool:
    try:
        response = requests.get(f"{server_url.rstrip('/')}/api/system_stats", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _append_comfy_log(settings: Mapping[str, Any], message: str) -> None:
    log_path = Path(str(settings["comfy_log_file"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(message.rstrip() + "\n")


def managed_comfy_pid(settings: Mapping[str, Any]) -> int | None:
    """Return the recorded batcher-owned launcher PID, removing stale records."""
    pid_path = Path(str(settings["comfy_pid_file"]))
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def start_comfy_and_wait(settings: Mapping[str, Any], log_func=None) -> subprocess.Popen | None:
    """Start portable ComfyUI when needed and wait until its HTTP API responds."""
    log = log_func or print
    server_url = str(settings["server_url"])
    _append_comfy_log(
        settings, f"\n=== Connection request {time.strftime('%Y-%m-%d %H:%M:%S')} ==="
    )
    log(f"ComfyUI console log: {settings['comfy_log_file']}")
    if check_comfy_connection(server_url):
        log(f"ComfyUI is already connected at {server_url}.")
        _append_comfy_log(
            settings,
            "Server was already running, so this batcher did not launch it and cannot capture its existing console output.",
        )
        return None
    launcher = str(settings["launcher"])
    parent = str(settings["comfy_parent_path"])
    log(f"Starting ComfyUI with {Path(launcher).name}...")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", launcher]
    else:
        command = [launcher]
    try:
        log_path = Path(str(settings["comfy_log_file"]))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as console_log:
            console_log.write(f"\n\n=== ComfyUI launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            console_log.flush()
            process = subprocess.Popen(
                command,
                cwd=parent,
                stdin=subprocess.DEVNULL,
                stdout=console_log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        try:
            Path(str(settings["comfy_pid_file"])).write_text(str(process.pid), encoding="utf-8")
        except OSError:
            process.terminate()
            raise
    except OSError as exc:
        raise ConnectionError(f"Could not start ComfyUI using '{launcher}': {exc}") from exc

    deadline = time.monotonic() + float(settings["startup_timeout_seconds"])
    while time.monotonic() < deadline:
        if check_comfy_connection(server_url):
            log(f"Connected to ComfyUI at {server_url}.")
            return process
        exit_code = process.poll()
        if exit_code is not None:
            raise ConnectionError(
                f"ComfyUI launcher exited with code {exit_code} before the server became ready."
            )
        time.sleep(1.0)
    stop_managed_comfy(settings, process, log)
    raise ConnectionError(
        f"ComfyUI did not become ready within {settings['startup_timeout_seconds']:g} seconds."
    )


def stop_managed_comfy(
    settings: Mapping[str, Any], process: subprocess.Popen | None = None, log_func=None
) -> bool:
    """Stop only a ComfyUI launcher previously started and recorded by this batcher."""
    log = log_func or print
    pid = process.pid if process is not None and process.poll() is None else managed_comfy_pid(settings)
    if pid is None:
        log("No batcher-managed ComfyUI process is running.")
        return False
    log(f"Stopping batcher-managed ComfyUI process tree (PID {pid})...")
    graceful = False
    if process is not None and process.poll() is None and os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=10)
            graceful = True
        except (OSError, subprocess.TimeoutExpired):
            graceful = False
    elif process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=10)
            graceful = True
        except (OSError, subprocess.TimeoutExpired):
            graceful = False

    if not graceful and os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0 and check_comfy_connection(str(settings["server_url"]), timeout=1):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    elif not graceful and process is not None:
        process.kill()

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and check_comfy_connection(str(settings["server_url"]), timeout=1):
        time.sleep(0.5)
    try:
        Path(str(settings["comfy_pid_file"])).unlink(missing_ok=True)
    except OSError:
        pass
    stopped = not check_comfy_connection(str(settings["server_url"]), timeout=1)
    log("ComfyUI stopped and its model memory was released." if stopped else "ComfyUI process was stopped, but the server still responds.")
    _append_comfy_log(settings, f"=== Stop requested {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    return stopped


def validate_output_folder_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigurationError("Output folder name cannot be empty.")
    if cleaned in {".", ".."} or any(character in cleaned for character in '<>:"/\\|?*'):
        raise ConfigurationError(
            "Output folder name must be one folder name and cannot contain < > : \" / \\ | ? *."
        )
    if cleaned.endswith((" ", ".")):
        raise ConfigurationError("Output folder name cannot end with a space or period.")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if cleaned.upper() in reserved:
        raise ConfigurationError(f"'{cleaned}' is a reserved Windows folder name.")
    return cleaned


def output_path_for(settings: Mapping[str, Any], folder_name: str) -> Path:
    return Path(str(settings["output_folder"])) / validate_output_folder_name(folder_name)


def load_batch_config(path: os.PathLike[str] | str) -> dict[str, Any]:
    config = _load_json(path, "Batch config file")
    if not isinstance(config, dict):
        raise ConfigurationError("Batch config must be a JSON object.")
    characters = config.get("characters")
    if not isinstance(characters, list) or not characters:
        raise ConfigurationError("Batch config must contain a non-empty 'characters' array.")
    base_prompt = config.get("base_additional_prompt", "")
    if not isinstance(base_prompt, str):
        raise ConfigurationError("'base_additional_prompt' must be a string.")
    normalized = []
    for index, item in enumerate(characters):
        label = f"characters[{index}]"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{label} must be an object.")
        name, prompt, runs = item.get("name", "Unnamed"), item.get("prompt", ""), item.get("runs", 1)
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"{label}.name must be a non-empty string.")
        if not isinstance(prompt, str):
            raise ConfigurationError(f"{label}.prompt must be a string.")
        if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
            raise ConfigurationError(f"{label}.runs must be an integer of at least 1.")
        normalized.append({"name": name, "prompt": prompt, "runs": runs})
    return {**config, "base_additional_prompt": base_prompt, "characters": normalized}


def load_workflow(path: os.PathLike[str] | str) -> dict[str, dict[str, Any]]:
    raw = _load_json(path, "Workflow file")
    if not isinstance(raw, dict):
        raise ConfigurationError("Workflow must be a JSON object exported in API format.")
    prompt = raw.get("prompt", raw)
    if not isinstance(prompt, dict) or not prompt:
        raise ConfigurationError("Workflow must contain API-format nodes, optionally inside 'prompt'.")
    for node_id, node in prompt.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise ConfigurationError(
                f"Workflow node '{node_id}' must have an 'inputs' object. Export it in API format."
            )
    return prompt


def _normalize_parameter_entry(node_title: str, raw_value: Any) -> dict[str, Any]:
    if not isinstance(node_title, str) or not node_title.strip():
        raise ConfigurationError("Every parameter node title must be a non-empty string.")
    if node_title.strip().lower() in RESERVED_NODE_TITLES:
        raise ConfigurationError(f"Parameter '{node_title}' is reserved and filled automatically.")
    if isinstance(raw_value, dict) and "input" in raw_value and "value" in raw_value:
        input_name = raw_value["input"]
        if not isinstance(input_name, str) or not input_name.strip():
            raise ConfigurationError(f"Parameter '{node_title}' has an invalid 'input'.")
        return {"node": node_title.strip(), "input": input_name.strip(), "value": raw_value["value"]}
    return {"node": node_title.strip(), "input": "value", "value": raw_value}


def normalize_parameters(raw: Any) -> list[dict[str, Any]]:
    """Accept a title/value object or an explicit parameter array."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [_normalize_parameter_entry(title, value) for title, value in raw.items()]
    if isinstance(raw, list):
        result = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ConfigurationError(f"parameters[{index}] must be an object.")
            if "node" not in entry or "value" not in entry:
                raise ConfigurationError(f"parameters[{index}] requires 'node' and 'value'.")
            result.append(_normalize_parameter_entry(entry["node"], {
                "input": entry.get("input", "value"), "value": entry["value"]
            }))
        return result
    raise ConfigurationError("Parameter file must be a JSON object or parameter array.")


def load_parameters(path: os.PathLike[str] | str) -> list[dict[str, Any]]:
    return normalize_parameters(_load_json(path, "Parameter file"))


def parameters_to_json(parameters: Iterable[Mapping[str, Any]]) -> dict[str, Any] | list[dict[str, Any]]:
    entries = list(parameters)
    titles = [str(entry["node"]) for entry in entries]
    if len({title.casefold() for title in titles}) != len(titles):
        return [{"node": str(entry["node"]), "input": str(entry.get("input", "value")),
                 "value": entry.get("value")} for entry in entries]
    result = {}
    for entry in entries:
        node, input_name, value = str(entry["node"]), str(entry.get("input", "value")), entry.get("value")
        result[node] = value if input_name == "value" else {"input": input_name, "value": value}
    return result


def _node_title(node: Mapping[str, Any]) -> str:
    meta = node.get("_meta", {})
    return str(meta.get("title", "")) if isinstance(meta, dict) else ""


def validate_parameter_targets(workflow, parameters) -> None:
    by_title = {}
    for node_id, node in workflow.items():
        title = _node_title(node).strip()
        if title:
            by_title.setdefault(title.casefold(), []).append((node_id, node))
    errors, seen = [], set()
    for entry in parameters:
        title, input_name = str(entry["node"]), str(entry.get("input", "value"))
        identity = (title.casefold(), input_name)
        if identity in seen:
            errors.append(f"Parameter '{title}.{input_name}' is listed more than once.")
            continue
        seen.add(identity)
        matches = by_title.get(title.casefold(), [])
        if not matches:
            errors.append(f"No workflow node has the title '{title}'.")
            continue
        missing = [node_id for node_id, node in matches if input_name not in node["inputs"]]
        if missing:
            errors.append(f"Node '{title}' has no input '{input_name}' (node IDs: {', '.join(missing)}).")
    if errors:
        raise ConfigurationError("Invalid workflow parameters:\n- " + "\n- ".join(errors))


def apply_parameters(workflow, parameters) -> None:
    for entry in parameters:
        wanted, input_name = str(entry["node"]).casefold(), str(entry.get("input", "value"))
        for node in workflow.values():
            if _node_title(node).strip().casefold() == wanted:
                node["inputs"][input_name] = copy.deepcopy(entry.get("value"))


def validate_automatic_targets(workflow) -> None:
    found = {"prompt": 0, "outputpath": 0, "charactername": 0}
    easy_positive_count = 0
    for node in workflow.values():
        title = _node_title(node).strip().lower()
        class_type, inputs = str(node.get("class_type", "")).lower(), node["inputs"]
        if class_type == "easy positive" and "positive" in inputs:
            easy_positive_count += 1
        if title == "prompt" and "positive" in inputs:
            found["prompt"] += 1
        elif title == "outputpath" and "value" in inputs:
            found["outputpath"] += 1
        elif title == "charactername" and "value" in inputs:
            found["charactername"] += 1
    if not found["prompt"] and easy_positive_count == 1:
        found["prompt"] = 1
    missing = [name for name, count in found.items() if not count]
    if missing:
        raise ConfigurationError(
            "Workflow is missing required automatic node title(s): " + ", ".join(missing)
            + ". Expected Prompt/positive, OutputPath/value, and CharacterName/value."
        )


def _apply_automatic_values(
    workflow, prompt_text: str, output_dir: str, run_name: str, seed: int
) -> int:
    titled_prompt_exists = any(
        _node_title(node).strip().lower() == "prompt" and "positive" in node["inputs"]
        for node in workflow.values()
    )
    easy_positive_nodes = [
        node for node in workflow.values()
        if str(node.get("class_type", "")).lower() == "easy positive" and "positive" in node["inputs"]
    ]
    seed_targets = 0
    for node in workflow.values():
        title = _node_title(node).strip().lower()
        class_type = str(node.get("class_type", "")).lower()
        is_prompt_fallback = not titled_prompt_exists and len(easy_positive_nodes) == 1 and node is easy_positive_nodes[0]
        if (title == "prompt" or is_prompt_fallback) and "positive" in node["inputs"]:
            node["inputs"]["positive"] = prompt_text
        elif title == "outputpath" and "value" in node["inputs"]:
            node["inputs"]["value"] = output_dir
        elif title == "charactername" and "value" in node["inputs"]:
            node["inputs"]["value"] = run_name
        if title == "seed":
            if "value" in node["inputs"]:
                node["inputs"]["value"] = seed
                seed_targets += 1
            elif "seed" in node["inputs"]:
                node["inputs"]["seed"] = seed
                seed_targets += 1
    return seed_targets


def _concat_prompts(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def load_presets(path: os.PathLike[str] | str) -> list[dict[str, str]]:
    preset_path = Path(path).expanduser().resolve()
    raw = _load_json(preset_path, "Preset index file")
    if not isinstance(raw, list):
        raise ConfigurationError("Preset index must be a JSON array.")
    presets, names, errors = [], set(), []
    for index, item in enumerate(raw):
        label = f"presets[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name must be a non-empty string.")
            continue
        if name.casefold() in names:
            errors.append(f"Preset name '{name}' is duplicated.")
            continue
        names.add(name.casefold())
        resolved = {"name": name}
        for key in ("workflow", "parameters"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}.{key} must be a non-empty path string.")
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = preset_path.parent / candidate
            candidate = candidate.resolve()
            if not candidate.is_file():
                errors.append(f"Preset '{name}' {key} file not found: {candidate}")
            resolved[key] = str(candidate)
        if "workflow" in resolved and "parameters" in resolved:
            try:
                workflow_data = load_workflow(resolved["workflow"])
                parameter_data = load_parameters(resolved["parameters"])
                validate_automatic_targets(workflow_data)
                validate_parameter_targets(workflow_data, parameter_data)
            except (OSError, ConfigurationError) as exc:
                errors.append(f"Preset '{name}' is invalid: {exc}")
            else:
                presets.append(resolved)
    if errors:
        raise ConfigurationError("Invalid preset index:\n- " + "\n- ".join(errors))
    return presets


def resolve_preset(path, name: str) -> dict[str, str]:
    for preset in load_presets(path):
        if preset["name"].casefold() == name.casefold():
            return preset
    raise ConfigurationError(f"Preset '{name}' was not found in {Path(path)}.")


def queue_workflow(config_path, workflow_path, output_dir, parameters=None, *, log_func=None,
                   comfy_api=DEFAULT_COMFY_API, request_timeout=10.0) -> None:
    log: Callable[[str], None] = log_func or print
    config, base_prompt = load_batch_config(config_path), load_workflow(workflow_path)
    normalized_parameters = normalize_parameters(parameters)
    validate_automatic_targets(base_prompt)
    validate_parameter_targets(base_prompt, normalized_parameters)
    api = comfy_api.rstrip("/")
    log("Checking ComfyUI connection...")
    try:
        response = requests.get(f"{api}/api/system_stats", timeout=request_timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ConnectionError(f"Could not connect to ComfyUI at {api}: {exc}") from exc
    log("Connected to ComfyUI.")
    used_seeds: set[int] = set()
    for character in config["characters"]:
        name, runs = character["name"], character["runs"]
        final_prompt = _concat_prompts(character["prompt"], config["base_additional_prompt"])
        log(f"Starting '{name}' ({runs} run{'s' if runs != 1 else ''}).")
        for index in range(runs):
            prompt, run_name = copy.deepcopy(base_prompt), f"{name}_{index}"
            seed = secrets.randbelow(MAX_AUTOMATIC_SEED + 1)
            while seed in used_seeds:
                seed = secrets.randbelow(MAX_AUTOMATIC_SEED + 1)
            used_seeds.add(seed)
            apply_parameters(prompt, normalized_parameters)
            seed_targets = _apply_automatic_values(prompt, final_prompt, str(output_dir), run_name, seed)
            try:
                response = requests.post(f"{api}/prompt", json={"prompt": prompt}, timeout=request_timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                details = getattr(getattr(exc, "response", None), "text", "")
                raise ConnectionError(f"Failed to queue '{run_name}': {exc}. {details}".rstrip()) from exc
            try:
                prompt_id = response.json().get("prompt_id", "unknown")
            except ValueError:
                prompt_id = "unknown"
            seed_note = f", seed: {seed}" if seed_targets else ""
            log(f"Queued '{run_name}' ({index + 1}/{runs}), prompt ID: {prompt_id}{seed_note}")
            time.sleep(0.3)
    log("All jobs were queued successfully.")


def _parse_cli_parameter(text: str) -> dict[str, Any]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("Expected NODE=JSON_VALUE or NODE::INPUT=JSON_VALUE.")
    target, raw_value = text.split("=", 1)
    node, input_name = target.split("::", 1) if "::" in target else (target, "value")
    if not node.strip() or not input_name.strip():
        raise argparse.ArgumentTypeError("Node title and input name cannot be empty.")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return {"node": node.strip(), "input": input_name.strip(), "value": value}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ComfyCharacterBatcher",
        description="Start ComfyUI when needed and queue API-format workflows without opening the GUI.",
        epilog=("Examples:\n"
                "  ComfyCharacterBatcher.exe --config batch.json --output-name MyFolder --preset \"Flux Images\"\n"
                "  ComfyCharacterBatcher.exe --config batch.json --output-name MyFolder --workflow workflow.json "
                "--parameters params.json --set \"Desired Height=768\"\n\n"
                "--set values are parsed as JSON when possible, preserving numbers and booleans."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Batch config JSON containing prompts and runs.")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output-name", help="One folder name created under ComfyUI/output.")
    output.add_argument("--output", help=argparse.SUPPRESS)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--preset", help="Preset name from the preset index.")
    source.add_argument("--workflow", help="API-format ComfyUI workflow JSON.")
    parser.add_argument("--parameters", help="Parameter-map JSON (used with --workflow).")
    parser.add_argument("--set", dest="overrides", action="append", default=[], type=_parse_cli_parameter,
                        metavar="NODE[::INPUT]=VALUE", help="Set/override one node input; repeat as needed.")
    parser.add_argument("--presets-file", default=str(application_directory() / DEFAULT_PRESET_FILE),
                        help="Preset index JSON; defaults to presets.json beside the executable.")
    parser.add_argument("--global-settings", default=str(application_directory() / DEFAULT_GLOBAL_SETTINGS_FILE),
                        help="Global settings JSON; defaults beside the executable.")
    parser.add_argument("--server", help="Override the ComfyUI URL from global settings.")
    parser.add_argument("--no-start-comfy", action="store_true",
                        help="Do not launch ComfyUI; require an already-running server.")
    parser.add_argument("--stop-comfy", action="store_true",
                        help="Stop a ComfyUI process previously started by this batcher, then exit.")
    parser.add_argument("--log-file", help="Write headless progress and errors to this file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    log_path = Path(args.log_file).expanduser() if args.log_file else (
        application_directory() / "batcher_headless.log" if getattr(sys, "frozen", False) else None
    )

    def log(message: str, *, error: bool = False) -> None:
        stream = sys.stderr if error else sys.stdout
        if stream is not None:
            print(message, file=stream)
        if log_path is not None:
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
            except OSError:
                pass

    try:
        settings = load_global_settings(args.global_settings)
        if args.server:
            settings["server_url"] = args.server.rstrip("/")
        if args.stop_comfy:
            stop_managed_comfy(settings, log_func=log)
            return 0
        missing = []
        if not args.config:
            missing.append("--config")
        if not (args.output_name or args.output):
            missing.append("--output-name")
        if not (args.preset or args.workflow):
            missing.append("--preset or --workflow")
        if missing:
            raise ConfigurationError("Missing required batch option(s): " + ", ".join(missing) + ".")
        if args.preset and args.parameters:
            raise ConfigurationError("--parameters cannot be combined with --preset; use --set for preset overrides.")
        if args.preset:
            preset = resolve_preset(args.presets_file, args.preset)
            workflow_path, parameters = preset["workflow"], load_parameters(preset["parameters"])
        else:
            workflow_path = args.workflow
            parameters = load_parameters(args.parameters) if args.parameters else []
        overrides = {(item["node"].casefold(), item["input"]): item for item in args.overrides}
        combined = [item for item in parameters
                    if (item["node"].casefold(), item["input"]) not in overrides] + list(overrides.values())
        if args.output_name:
            output_path = output_path_for(settings, args.output_name)
        else:
            output_path = Path(args.output).expanduser()
        # Fail fast on local input errors before starting a potentially expensive GPU service.
        load_batch_config(args.config)
        workflow_data = load_workflow(workflow_path)
        normalized = normalize_parameters(combined)
        validate_automatic_targets(workflow_data)
        validate_parameter_targets(workflow_data, normalized)
        if not args.no_start_comfy:
            start_comfy_and_wait(settings, log)
        output_path.mkdir(parents=True, exist_ok=True)
        queue_workflow(args.config, workflow_path, output_path, combined,
                       comfy_api=settings["server_url"], log_func=log)
        return 0
    except (OSError, ConfigurationError, ConnectionError) as exc:
        log(f"Error: {exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
