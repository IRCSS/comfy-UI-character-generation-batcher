
# ComfyUI Batch Caller

**ComfyUI Batch Caller** is a small Python tool that talks to a running ComfyUI server and submits a workflow many times with different prompts/settings. It was originally written for text-to-3D rigged character generation, but you can use it for **any Comfy workflow** as long as you expose a few “easy-use” nodes. If you want to just use the caller and don't care about the code, grab it from releases but make sure to read the how to use: https://github.com/IRCSS/comfy-UI-character-generation-batcher/releases/tag/Release

## What this repo gives you

- A Python batch runner that:
  - loads a Comfy **API-ready** workflow JSON
  - reads a batch config JSON
  - edits specific nodes (Prompt / OutputPath / CharacterName)
  - queues runs via Comfy’s HTTP API
- Example configs/workflows for:
  - text → image → Hunyuan3D → Blender rigging
  - generic batch image/mesh generation

## Requirements

- Python 3.9+
- `requests`
- A running ComfyUI instance

If you want to extend the code, have a look at the guide.txt for few simple things to keep in mind. 

## How to use
You need to provide it with a workflow file, output path (needs to be in the output folder of Comfy) and a config file. 
The config looks like this:

```
{
  "base_additional_prompt": ", T-pose, studio lighting, clean background, full body",
  "characters": [
    {
      "name": "Elf Warrior",
      "prompt": "A tall elf warrior in silver armor, fantasy style",
      "runs": 2
    },
    {
      "name": "Cyberpunk Hacker",
      "prompt": "A young cyberpunk hacker with neon lights",
      "runs": 1
    }
  ]
} 
```

It will take the prompt + base_aditional_prompt with the title name and run in as many times as runs indicates for every entry of this array. 

If you are sharing workflows across different machines there are some things you need to keep in mind. All models in the workflow need to be installed in both machines and under the same relative paths. Similarly any other resources used such as absolute/ relative paths need to be the same between machines. For example the workflow I provided in this repo will only work if you have everything installed like I have, and have certain images in the Input folder of Comfy for the workflow to find. 

Realisticly, the workflows I have here are just examples. You would need to make your own. Here is an important point, the batcher expects API exported workflows which you can do if you turn on developer options in Comfy. When you are making a new workflow, you just need a comfy-easy-use node titled Prompt, one titled OutputPath, and one titled CharacterName. These will be set as the prompt, output folder, and run name from the batcher input. You can wire them however you want. With that, the same batcher can generate audio or whatever else you fancy. If you want to see an example of this, look at the non API version of my workflows provided here: https://github.com/IRCSS/comfyUI-blender-wrapper

I have explained the whole thing alot more indepth and my usage here: https://medium.com/@shahriyarshahrabi/blender-as-a-pipeline-engine-make-rigged-characters-with-comfyui-3a1e81a3e623