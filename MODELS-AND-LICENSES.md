# Models and licenses

Stillive ships **no model weights**. Every model below is downloaded by the user, from its official page, during the preparation step on the computer. Stillive runs everything locally: no cloud service, no partner node.

Licenses read on the official pages on 26 October 2026.

| Component | Used for | Source | License | Commercial use |
|---|---|---|---|---|
| MiniMax H3 (video, image to video and reference to video, with sound) | Animating the photo, music and ambient sound | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) (repackaged for ComfyUI), original [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) | [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | Yes, with conditions (see below) |
| MiniMax H3 turbo LoRAs (4 steps) | Faster video | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | Same repository license; no separate license is shown on the page ❓ | ❓ |
| Gemma 4 12B instruct, GGUF quantizations (with its vision projector and MTP draft file) | Understanding the photo and writing the video prompt | [unsloth/gemma-4-12b-it-GGUF](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF), MTP draft file from [unsloth/gemma-4-12B-it-qat-GGUF](https://huggingface.co/unsloth/gemma-4-12B-it-qat-GGUF) (Apache-2.0), original [google/gemma-4-12B-it](https://huggingface.co/google/gemma-4-12B-it) | Apache-2.0 (as stated on the original model page, checked 2026-10-26) | Yes |
| Qwen Image 2.1 (optional, not downloaded by Stillive; used only if already present in ComfyUI, with the Qwen3-VL 8B text encoder) | Restoring and colorizing old photos before animation | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1), original [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) | Qwen Research License (see the model page) | See the model page |
| llama.cpp (build b11821) | Running Gemma locally | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT | Yes |
| imageio-ffmpeg | Video editing (ffmpeg) | [imageio/imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | BSD-2-Clause (the bundled ffmpeg binary has its own license ❓) | Yes |
| Fraunces, Caveat, DM Sans fonts | Interface and video titles | Google Fonts | SIL Open Font License 1.1 (`stillive/web/fonts/LICENSE-polices.txt`) | Yes |
| qrcode-generator 2.0.4 | QR code on the computer page | [npm](https://www.npmjs.com/package/qrcode-generator) | MIT (`stillive/web/vendor/LICENSE-qrcode-generator.txt`) | Yes |

## MiniMax H3: conditions to know

From the official license text:

- **Over 20 million US dollars** of revenue: separate written authorization from MiniMax.
- Commercial products must display "MiniMax H3" in their interface.
- Any redistribution of the H3 files must include the license and this notice: "MiniMax H3 is licensed under the MiniMax H3 Community License Agreement, Copyright © 2026 MiniMax. All Rights Reserved." Stillive does not redistribute them: they are downloaded from Hugging Face.

The weights are published openly on Hugging Face (open weights), under this community license, which is not a standard open source license.
