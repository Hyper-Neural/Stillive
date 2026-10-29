# Stillive (application folder)

See the [main README](../README.md) for what Stillive does and how to install it.

## Structure
- `stillive/serveur.py`: the web server the phone talks to (runs inside ComfyUI).
- `stillive/analyse.py`: photo understanding and H3 prompt writing (Gemma 4 through llama.cpp).
- `stillive/clip.py`: one photo to one clip (analysis, optional restoration, H3, editing).
- `stillive/h3.py`: MiniMax H3 graphs (image to video, reference to video, turbo LoRA).
- `stillive/comfy.py`: drives ComfyUI through its HTTP and WebSocket API.
- `comfy_nodes/`: the Stillive ComfyUI nodes and sidebar panel.
- `web/`: the phone and computer pages (no build step), texts in `web/i18n/`.
- `workflows/`: the H3 graphs Stillive sends, in ComfyUI API format.
- `outils/`: command line tools (one H3 shot, analysis, clip).
