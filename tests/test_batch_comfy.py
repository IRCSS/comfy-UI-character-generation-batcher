import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import batch_comfy


WORKFLOW = {
    "1": {"class_type": "easy positive", "inputs": {"positive": "old"}, "_meta": {"title": "Prompt"}},
    "2": {"class_type": "easy string", "inputs": {"value": "old"}, "_meta": {"title": "OutputPath"}},
    "3": {"class_type": "easy string", "inputs": {"value": "old"}, "_meta": {"title": "CharacterName"}},
    "4": {"class_type": "easy int", "inputs": {"value": 1}, "_meta": {"title": "Desired Height"}},
    "5": {"class_type": "LoadImage", "inputs": {"image": "old.png"}, "_meta": {"title": "Load Image"}},
    "6": {"class_type": "easy positive", "inputs": {"positive": "keep pose instructions"}, "_meta": {"title": "Base"}},
    "7": {"class_type": "SeedNode", "inputs": {"seed": 2}, "_meta": {"title": "Seed"}},
}


class Response:
    text = ""

    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class BatcherTests(unittest.TestCase):
    def test_parameter_formats_preserve_types(self):
        result = batch_comfy.normalize_parameters({
            "Desired Height": 768,
            "Load Image": {"input": "image", "value": "pose.png"},
        })
        self.assertEqual(result[0]["value"], 768)
        self.assertIsInstance(result[0]["value"], int)
        self.assertEqual(result[1]["input"], "image")

    def test_reserved_parameters_are_rejected(self):
        with self.assertRaisesRegex(batch_comfy.ConfigurationError, "reserved"):
            batch_comfy.normalize_parameters({"Prompt": "override"})
        with self.assertRaisesRegex(batch_comfy.ConfigurationError, "reserved"):
            batch_comfy.normalize_parameters({"Seed": 123})

    def test_target_validation_is_actionable(self):
        with self.assertRaisesRegex(batch_comfy.ConfigurationError, "No workflow node"):
            batch_comfy.validate_parameter_targets(WORKFLOW, [{"node": "Missing", "input": "value", "value": 1}])
        with self.assertRaisesRegex(batch_comfy.ConfigurationError, "has no input"):
            batch_comfy.validate_parameter_targets(WORKFLOW, [{"node": "Load Image", "input": "value", "value": 1}])

    def test_apply_parameters(self):
        workflow = copy.deepcopy(WORKFLOW)
        params = batch_comfy.normalize_parameters({
            "Desired Height": 1024,
            "Load Image": {"input": "image", "value": "new.png"},
        })
        batch_comfy.validate_parameter_targets(workflow, params)
        batch_comfy.apply_parameters(workflow, params)
        self.assertEqual(workflow["4"]["inputs"]["value"], 1024)
        self.assertEqual(workflow["5"]["inputs"]["image"], "new.png")

    def test_preset_paths_are_relative_to_index(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            (folder / "params.json").write_text('{"Desired Height": 512}', encoding="utf-8")
            (folder / "presets.json").write_text(json.dumps([
                {"name": "Test", "workflow": "workflow.json", "parameters": "params.json"}
            ]), encoding="utf-8")
            preset = batch_comfy.load_presets(folder / "presets.json")[0]
            self.assertEqual(Path(preset["workflow"]), (folder / "workflow.json").resolve())
            self.assertEqual(Path(preset["parameters"]), (folder / "params.json").resolve())

    def test_preset_parameters_are_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            entries = [
                {"name": "Omitted", "workflow": "workflow.json"},
                {"name": "Null", "workflow": "workflow.json", "parameters": None},
                {"name": "Empty", "workflow": "workflow.json", "parameters": ""},
            ]
            (folder / "presets.json").write_text(json.dumps(entries), encoding="utf-8")
            presets = batch_comfy.load_presets(folder / "presets.json")
            self.assertEqual([preset["parameters"] for preset in presets], [None, None, None])

    def test_preset_workflow_remains_required(self):
        with tempfile.TemporaryDirectory() as directory:
            preset_path = Path(directory) / "presets.json"
            preset_path.write_text('[{"name": "Missing workflow", "parameters": ""}]', encoding="utf-8")
            with self.assertRaisesRegex(batch_comfy.ConfigurationError,
                                        r"presets\[0\]\.workflow must be a non-empty path string"):
                batch_comfy.load_presets(preset_path)

    @patch("batch_comfy.time.sleep")
    @patch("batch_comfy.secrets.randbelow", return_value=987654)
    @patch("batch_comfy.requests.post")
    @patch("batch_comfy.requests.get")
    def test_queue_payload_contains_automatic_and_generic_values(self, get, post, _seed, _sleep):
        get.return_value = Response()
        post.return_value = Response({"prompt_id": "abc"})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = {"base_additional_prompt": "extra", "characters": [
                {"name": "Asset", "prompt": "main", "runs": 1}
            ]}
            (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (folder / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            batch_comfy.queue_workflow(folder / "config.json", folder / "workflow.json", "Output", {"Desired Height": 900})
        prompt = post.call_args.kwargs["json"]["prompt"]
        self.assertEqual(prompt["1"]["inputs"]["positive"], "main extra")
        self.assertEqual(prompt["6"]["inputs"]["positive"], "keep pose instructions")
        self.assertEqual(prompt["2"]["inputs"]["value"], "Output")
        self.assertEqual(prompt["3"]["inputs"]["value"], "Asset_0")
        self.assertEqual(prompt["4"]["inputs"]["value"], 900)
        self.assertEqual(prompt["7"]["inputs"]["seed"], 987654)
        _seed.assert_called_once_with(1_000_000)

    def test_global_settings_and_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "ComfyUI").mkdir()
            (folder / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
            (folder / "run_nvidia_gpu.bat").write_text("@echo off", encoding="utf-8")
            settings_path = folder / "global_settings.json"
            settings_path.write_text(json.dumps({
                "comfy_parent_path": str(folder),
                "launcher": "run_nvidia_gpu.bat",
            }), encoding="utf-8")
            settings = batch_comfy.load_global_settings(settings_path)
            self.assertEqual(batch_comfy.output_path_for(settings, "Underwater"),
                             folder / "ComfyUI" / "output" / "Underwater")
            self.assertEqual(Path(settings["comfy_log_file"]), folder / "comfyui_console.log")
            self.assertEqual(Path(settings["comfy_pid_file"]), folder / "comfyui_managed.pid")

    def test_output_folder_name_rejects_paths(self):
        for value in ("", "../outside", "nested/folder", "CON", "bad:name"):
            with self.subTest(value=value), self.assertRaises(batch_comfy.ConfigurationError):
                batch_comfy.validate_output_folder_name(value)

    @patch("batch_comfy.time.sleep")
    @patch("batch_comfy.secrets.randbelow", side_effect=[5, 5, 6])
    @patch("batch_comfy.requests.post")
    @patch("batch_comfy.requests.get")
    def test_seeds_are_unique_within_a_batch(self, get, post, _seed, _sleep):
        get.return_value = Response()
        post.return_value = Response({"prompt_id": "abc"})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = {"characters": [{"name": "Asset", "prompt": "main", "runs": 2}]}
            (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (folder / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            batch_comfy.queue_workflow(folder / "config.json", folder / "workflow.json", "Output")
        seeds = [call.kwargs["json"]["prompt"]["7"]["inputs"]["seed"] for call in post.call_args_list]
        self.assertEqual(seeds, [5, 6])

    @patch("batch_comfy.time.sleep")
    @patch("batch_comfy.subprocess.Popen")
    @patch("batch_comfy.check_comfy_connection", side_effect=[False, True])
    def test_start_comfy_waits_for_connection(self, _connected, popen, _sleep):
        popen.return_value.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "server_url": "http://127.0.0.1:8188",
                "launcher": r"D:\\Comfy\\run_nvidia_gpu.bat",
                "comfy_parent_path": r"D:\\Comfy",
                "startup_timeout_seconds": 5,
                "comfy_log_file": str(Path(directory) / "comfy.log"),
                "comfy_pid_file": str(Path(directory) / "comfy.pid"),
            }
            popen.return_value.pid = 12345
            process = batch_comfy.start_comfy_and_wait(settings)
            self.assertEqual((Path(directory) / "comfy.pid").read_text(encoding="utf-8"), "12345")
        self.assertIs(process, popen.return_value)
        popen.assert_called_once()

    @patch("batch_comfy.check_comfy_connection", return_value=False)
    def test_stop_managed_comfy_uses_owned_process(self, _connected):
        process = unittest.mock.Mock()
        process.pid = 12345
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "comfy.pid"
            pid_file.write_text("12345", encoding="utf-8")
            settings = {
                "server_url": "http://127.0.0.1:8188",
                "comfy_pid_file": str(pid_file),
                "comfy_log_file": str(Path(directory) / "comfy.log"),
            }
            self.assertTrue(batch_comfy.stop_managed_comfy(settings, process))
            self.assertFalse(pid_file.exists())
        process.send_signal.assert_called_once()


if __name__ == "__main__":
    unittest.main()
