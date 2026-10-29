# Stillive

**Your photos come alive. Nothing leaves your home.**

Stillive turns a single photo (a family picture, a vintage print, a phone snapshot) into a short animated clip with music and ambient sound. It runs on your own computer with ComfyUI, and you use it from your phone over your home Wi-Fi.

## What you can do

**Bring a memory back to life**: the AI looks at the photo, imagines a natural moment and animates it.
- *Bring my photo to life*: "Natural" or "Surprise me!".
- *Just for fun*: Dance, Change era (pirates, Versailles, the 80s… or any year), Teleportation (the Moon, Times Square, northern lights… or any place), Change season, Cartoon or claymation, Back to color, Living portrait (seamless loop), Tell your story (write what happens).

**Step into a memory**: take a selfie and appear inside any photo, for example your parents' childhood picture.
- Automatic, Selfie with them, Balloon arrival, Dance with them, Superhero entrance, Magic portal, Reunion, Out of the scenery, Tarzan vine, Electric scooter, Glitch appearance, Hug your younger self.

Clips are short (5 seconds) or long (10 seconds), with an optional little note at the end. Each clip comes in a vertical and a horizontal version. "My memories" keeps all your clips; download any of them in one tap.

## What you need

- A Windows or Linux computer with an NVIDIA graphics card with at least 12 GB of video memory, and 32 GB of RAM.
- Free disk space for the models, downloaded automatically: about 50 GB, plus 23 GB for "Step into a memory" (optional), plus about 5 GB for ComfyUI if you don't have it yet.
- A phone on the same Wi-Fi network as the computer.

## Install

1. Download this repository (green "Code" button, then "Download ZIP") and unzip it.
2. Double-click `installer/INSTALLER.bat` (on Linux: `python3 installer/install_stillive.py`). It needs Python 3.9 or later and tells you how to get it if it is missing.
3. Click "Install Stillive".
   - **You don't have ComfyUI**: the installer sets it up for you, adds Stillive, then opens the preparation page.
   - **You already have ComfyUI**: choose your ComfyUI folder in advanced mode; only Stillive is added. Restart ComfyUI, then click "Set up Stillive" in the small message ComfyUI shows.
4. The preparation page downloads the models (this can take a while), then shows a QR code.
5. Scan the QR code with your phone. The first time, Windows may ask to allow Python on the network: click "Allow" (private network).

## Everyday use

- Start: the "Stillive" shortcut on the desktop (or `start_stillive.bat` in the install folder). Stop: `stop_stillive.bat`.
- On the phone: open the saved page, pick a photo (or scan a paper photo), choose an effect, wait a few minutes, watch and download.
- Update: download the new version and run the installer again. Your settings, models and clips are kept.
- Uninstall: on the computer, open the preparation page, section "Uninstall Stillive", and choose what to remove. It only removes what Stillive installed itself, never your own models.

## Privacy

- Your photos and clips stay on your computer. Nothing is sent online.
- The phone page only opens with the secret code inside the QR code.
- Settings, installation and uninstallation are only available on the computer itself.

## How it works

1. **Gemma 4 12B** (run locally by llama.cpp inside a Stillive ComfyUI node) looks at the photo and writes a precise, timed prompt for the chosen effect.
2. **MiniMax H3** turns the photo into a video with its own music and ambient sound (image to video, or reference to video with your selfie for "Step into a memory"), using the official 4-step turbo LoRA.
3. Stillive trims the clip, adds your note and sends it to your phone.

The H3 graphs Stillive sends to ComfyUI are in [`stillive/workflows/`](stillive/workflows/) (ComfyUI API format).

## Models and licenses

Stillive ships no model weights; they are downloaded from their official pages. See [MODELS-AND-LICENSES.md](MODELS-AND-LICENSES.md).

## License

Stillive's code is released under the GNU Affero General Public License v3.0 ([LICENSE](LICENSE)).
