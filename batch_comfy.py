import json
import requests
import os
import time
import copy

COMFY_API = "http://127.0.0.1:8188"

def _concat_prompts(*parts: str) -> str:
    """Join prompt parts with a single space, skipping empties and trimming."""
    cleaned = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return " ".join(cleaned)


def queue_workflow(config_path, workflow_path, output_dir, log_func=None):
    """Batch-queues ComfyUI workflows directly via the /prompt API."""

    def log(msg):
        if log_func:
            log_func(msg)
        else:
            print(msg)

    # --- Load Config ---
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")

    if "characters" not in config:
        raise ValueError("Config file must contain a 'characters' array.")
    
    base_additional = config.get("base_additional_prompt", "")
    if base_additional is not None and not isinstance(base_additional, str):
        raise ValueError("'base_additional_prompt' must be a string if provided.")
    
    # --- Load Workflow (exported in API format) ---
    if not os.path.exists(workflow_path):
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        base_prompt = json.load(f)

    # --- Verify ComfyUI API connection ---
    log("🔍 Checking ComfyUI connection...")
    try:
        r = requests.get(f"{COMFY_API}/api/system_stats", timeout=3)
        if r.status_code == 200:
            log("✅ Connected to ComfyUI!\n")
        else:
            raise ConnectionError("Unexpected response from ComfyUI API.")
    except Exception as e:
        raise ConnectionError(f"❌ Could not connect to ComfyUI: {e}")

    # --- Loop over characters ---
    for character in config["characters"]:
        name = character.get("name", "Unnamed")
        prompt_text = character.get("prompt", "")
        runs = int(character.get("runs", 1))

        # Build final prompt: character prompt + shared base addition
        final_prompt = _concat_prompts(prompt_text, base_additional)
        log(f"🎨 Starting generation for '{name}' ({runs} runs)...")
        log(f"   ➜ Prompt: {final_prompt}")

        for i in range(runs):
            # Handle both wrapped and unwrapped API exports
            prompt_data = base_prompt.get("prompt", base_prompt)
            prompt = copy.deepcopy(prompt_data)
            run_name = f"{name}_{i}"

            # --- Update Easy-Use nodes ---
            for node_id, node in prompt.items():
                ctype = node.get("class_type", "").lower()
                title = node.get("_meta", {}).get("title", "").lower()

                # Easy-Use positive prompt
                if ctype == "easy positive" or title == "prompt":
                    node["inputs"]["positive"] = final_prompt

                # Easy-Use output path
                elif ctype == "easy string" and title == "outputpath":
                    node["inputs"]["value"] = output_dir

                # Easy-Use character name
                elif ctype == "easy string" and title == "charactername":
                    node["inputs"]["value"] = run_name

            # --- Send to ComfyUI ---
            data = {"prompt": prompt}
            response = requests.post(f"{COMFY_API}/prompt", json=data)

            if response.status_code == 200:
                rid = response.json().get("prompt_id", "unknown")
                log(f"✅ Queued '{run_name}' (run {i+1}) | prompt_id: {rid}")
            else:
                log(f"❌ Failed to queue '{run_name}' (run {i+1}): {response.status_code} {response.text}")

            time.sleep(0.3)

        log(f"✅ Finished all runs for '{run_name}'\n")

    log("🎉 All jobs queued successfully!\n")


if __name__ == "__main__":
    # --- Example run (adjust these paths for your setup) ---
    CONFIG_PATH = r"C:\Temp\CharacterGenerator\Source\config.json"
    WORKFLOW_PATH = r"C:\Temp\CharacterGenerator\Source\workflow_api.json"
    OUTPUT_DIR = r"C:\Temp\CharacterGenerator\Output"

    print("🚀 Starting batch process...")
    queue_workflow(CONFIG_PATH, WORKFLOW_PATH, OUTPUT_DIR)