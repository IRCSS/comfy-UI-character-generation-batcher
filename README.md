# ComfyUI Batch Caller

ComfyUI Batch Caller starts a configured portable ComfyUI installation, waits for its API, and submits an API-format workflow repeatedly while changing its prompt, generated name, output path, optional seed, and any additional inputs you configure. It works with image, mesh, character, audio, or other workflows.

This file is the ground-truth reference for the GUI, command-line interface, JSON formats, validation rules, and executable builds.

This was based on character generation with comfy plus rigging in blender. more info: [Blender as a Pipeline Engine](https://medium.com/@shahriyarshahrabi/blender-as-a-pipeline-engine-make-rigged-characters-with-comfyui-3a1e81a3e623).

## How it works

For each item in the prompt config, the batcher:

1. Loads a fresh copy of the selected API-format workflow.
2. Applies the reusable workflow parameter map.
3. Sets the prompt, output path, unique run name, and any titled `Seed` node automatically.
4. Sends the resulting workflow to ComfyUI's `/prompt` endpoint once per requested run.

The GUI can launch ComfyUI for you. The default server is `http://127.0.0.1:8188`.

## Global settings and ComfyUI connection

`global_settings.json` must be next to the Python source during development and next to the executable after packaging. For a fresh checkout, copy `presetAndConfigSettingExample/global_settings.json` to the repository root and edit the ComfyUI path. The root file contains machine-specific settings and is intentionally ignored by Git.

```json
{
  "comfy_parent_path": "D:\\ComfyUI_windows_portable_v0.36.0",
  "launcher": "run_nvidia_gpu.bat",
  "server_url": "http://127.0.0.1:8188",
  "startup_timeout_seconds": 300,
  "comfy_log_file": "comfyui_console.log",
  "comfy_pid_file": "comfyui_managed.pid"
}
```

- `comfy_parent_path` is required. It is the portable parent folder containing `ComfyUI`, `python_embeded`, and the launcher batch files.
- `launcher` defaults to `run_nvidia_gpu.bat` and may be an absolute path or a path relative to the parent folder.
- `server_url` defaults to the local ComfyUI address.
- `startup_timeout_seconds` defaults to five minutes.
- `comfy_log_file` defaults beside the settings file. ComfyUI's normally hidden console output is appended here so errors can be copied and searched.
- `comfy_pid_file` records ownership of a ComfyUI launcher started by the batcher, allowing that process tree to be stopped safely.

At startup the app validates the settings file, parent folder, `ComfyUI/main.py`, and launcher. **Start ComfyUI** runs the launcher in the background and changes the status to **Connected** only after `/api/system_stats` responds. **Stop ComfyUI** stops only a process launched by the batcher and releases its loaded model memory. Closing the GUI asks whether to stop a batcher-managed instance. If ComfyUI was already running externally, the batcher neither captures that old console nor stops that process. The connection panel displays the console-log path and provides a copy button.

## Required workflow nodes

Give the relevant nodes these displayed titles in ComfyUI before exporting the workflow for API use:

| Node title | Input changed by the batcher | Value |
| --- | --- | --- |
| `Prompt` | `positive` | Item prompt plus `base_additional_prompt` |
| `OutputPath` | `value` | Output folder supplied in the GUI or CLI |
| `CharacterName` | `value` | Item name plus a zero-based run suffix, such as `Chair_0` |
| `Seed` (optional) | `value`, or `seed` | A unique value from `0` through `999,999` for every queued run |

Only the node titled `Prompt` is changed when that title exists. This is important when a workflow has other `easy positive` nodes containing base or pose instructions. For compatibility, a single `easy positive` node is used as a fallback only when no titled `Prompt` exists. Title matching is case-insensitive. `Prompt`, `OutputPath`, `CharacterName`, and `Seed` are reserved and cannot be overridden in the generic map.

Always export the workflow through ComfyUI's **Export (API)** option. A normal UI workflow is not API-ready.

## Prompt config

The prompt config controls what is generated and how many times. It remains independent from workflow presets.

```json
{
  "base_additional_prompt": "T-pose, studio lighting, clean background",
  "characters": [
    {
      "name": "Elf Warrior",
      "prompt": "A tall elf warrior in silver armor",
      "runs": 2
    },
    {
      "name": "Stone Arch",
      "prompt": "An ancient moss-covered stone arch",
      "runs": 1
    }
  ]
}
```

- `base_additional_prompt` is optional and must be a string. It is appended to every item prompt with one separating space.
- `characters` is retained as the property name for backward compatibility. It can contain any kind of asset.
- `name` is a non-empty string and becomes the generated run name.
- `prompt` is a string.
- `runs` is an integer of at least `1`.

## Workflow parameter maps

The common form maps an exact node title to the value placed in that node's `value` input:

```json
{
  "Desired Height": 768,
  "Use Delight": true,
  "Model Name": "example_model.safetensors"
}
```

JSON types are preserved. Numbers stay numbers, booleans stay booleans, and objects or arrays remain structured JSON values.

For a node input that is not named `value`, use the explicit form:

```json
{
  "Load Image": {
    "input": "image",
    "value": "T-pose.png"
  },
  "Empty Latent Image": {
    "input": "width",
    "value": 1024
  }
}
```

If the same node title needs more than one input, use the array form because JSON object keys must be unique:

```json
[
  {"node": "Empty Latent Image", "input": "width", "value": 1024},
  {"node": "Empty Latent Image", "input": "height", "value": 1024}
]
```

The UI reads and writes both formats. It automatically uses the array form when duplicate node titles require it.

## Presets

`presets.json` belongs beside the Python source during development and beside the executables after packaging. It is a JSON array:

```json
[
  {
    "name": "Flux Image T-Pose Generator",
    "workflow": "workflows/flux_tpose_api.json",
    "parameters": "parameters/flux_tpose.json"
  },
  {
    "name": "Qwen Text to Rigged Character",
    "workflow": "workflows/qwen_character_api.json",
    "parameters": "parameters/qwen_character.json"
  }
]
```

Relative paths are resolved relative to `presets.json`, not the current working directory. Absolute paths also work. Preset names must be unique. At startup, every preset is validated: its workflow and parameter files must exist, contain valid JSON, meet their schemas, and reference real node titles and inputs.

Keep the index as `[]` if no defaults are configured. The UI will then show only **Custom**.

## GUI usage

1. Confirm the global settings file is valid, then select **Start ComfyUI** and wait for **Connected**.
2. Select the prompt config and enter one output folder name, such as `Underwater`.
3. Copy the displayed complete output path if needed. The folder is created when the batch starts.
4. Select a preset, or leave **Custom** selected and choose a workflow.
5. Add, edit, or remove parameter rows. Choose the correct data type for every value.
6. Use **Load** and **Save** above the map to reuse a parameter JSON file.
7. Select **Start Batch**.

Selecting a preset loads both its workflow and parameter map. Manually changing either switches the selection back to **Custom**. Hover over UI fields and buttons for contextual help.

## Headless usage

The main `ComfyCharacterBatcher.exe` switches to headless mode whenever command-line options are supplied. With no options it opens the normal GUI. The same dispatch is available through `python gui.py`. Headless mode validates global settings, starts ComfyUI when necessary, waits for it, creates the output folder, and queues the batch. Because the main executable is a Windows GUI application, use `Start-Process -Wait` when a PowerShell caller must wait for completion; progress and errors are also written to `batcher_headless.log`.

```powershell
ComfyCharacterBatcher.exe --config exampleConfig.json --output-name Characters --preset "Flux Image T-Pose Generator"
```

Use a custom workflow and parameter file:

```powershell
ComfyCharacterBatcher.exe --config exampleConfig.json --output-name Characters --workflow TextToTPoseImage.json --parameters exampleParameters.json
```

Parameters may also be passed or overridden directly. Repeat `--set` as needed:

```powershell
ComfyCharacterBatcher.exe --config exampleConfig.json --output-name Characters --workflow TextToTPoseImage.json --set "Desired Height=768" --set "Load Image::image=T-pose.png"
```

The syntax is `NODE=VALUE` for the usual `value` input and `NODE::INPUT=VALUE` for another input. Values are parsed as JSON when possible, so `768`, `true`, `null`, arrays, and objects keep their types; unquoted text is treated as a string.

Useful options:

- `--config PATH`: required prompt config.
- `--output-name NAME`: required single folder name under the configured `ComfyUI/output` folder.
- `--preset NAME`: select an entry from the preset index.
- `--workflow PATH`: use a workflow directly instead of a preset.
- `--parameters PATH`: parameter map used with `--workflow`.
- `--set NODE[::INPUT]=VALUE`: repeatable direct parameter or override.
- `--presets-file PATH`: use an index other than `presets.json` beside the executable.
- `--global-settings PATH`: use global settings other than the file beside the executable.
- `--server URL`: override the server in global settings.
- `--no-start-comfy`: require an existing connection instead of launching ComfyUI.
- `--stop-comfy`: stop a previously batcher-launched ComfyUI process and exit; no batch arguments are required.
- `--log-file PATH`: select a progress/error log. The windowed executable defaults to `batcher_headless.log` beside itself.
- `--help`: show the complete command help.

Successful runs return exit code `0`. Configuration, validation, or connection failures return a nonzero exit code and print a readable error to standard error.

The batcher returns after jobs have been accepted into ComfyUI's queue; it does not wait for image or mesh generation to finish. Do not call `--stop-comfy` immediately after a successful batch command. Wait until ComfyUI's queue is empty, then stop the managed server. The same caution applies when closing the GUI while generations are still running.

## Validation and errors

Validation happens before anything is queued. Errors identify the relevant file, JSON line and column when available, preset, character entry, node title, input name, or missing path. The batcher rejects:

- malformed or incorrectly shaped JSON;
- empty prompt batches or invalid run counts;
- non-API workflows;
- missing automatic nodes;
- reserved automatic fields in a parameter map;
- parameter titles or inputs that do not exist in the selected workflow;
- duplicate presets or parameter targets;
- unavailable ComfyUI servers and rejected queue requests.

## Development and packaging

Requirements are listed in `requirements.txt`. From the repository folder:

```powershell
python -m pip install -r requirements.txt
Copy-Item presetAndConfigSettingExample\global_settings.json global_settings.json
# Edit global_settings.json for this machine.
python -m unittest discover -s tests -v
python gui.py
```

Build the hybrid GUI/headless executable:

```powershell
python -m PyInstaller --noconfirm ComfyCharacterBatcher.spec
Copy-Item presets.json dist\presets.json
Copy-Item global_settings.json dist\global_settings.json
```

Place any relative workflow and parameter files referenced by `presets.json` under the same `dist` folder structure. A separate `ComfyBatcherCLI.spec` remains available for developers who prefer a visible console, but it is not required: the main windowed executable supports all headless options and records output in `batcher_headless.log`.

The two `.spec` files are source-controlled build definitions and should be committed. Generated `build/`, `dist/`, virtual-environment, log, PID, and machine-specific global-settings files are excluded by `.gitignore`.

## Environment notes

Workflow JSON does not bundle checkpoints, VAEs, custom nodes, images, Blender files, or other dependencies. Those resources must exist in the expected ComfyUI locations on the machine running the workflow. The output value must also be acceptable to the nodes used by that workflow; many ComfyUI setups require a subfolder of ComfyUI's output directory.


